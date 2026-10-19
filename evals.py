#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute==2.13.5"]
# ///
"""Evaluate the dispute-review agent twice: on point-in-time context and on today's values.

The task and both scorers are Chalk functions; importing this file deploys them. Run it from a
directory whose your existing `chalk login` points at the environment (or export CHALK_CLIENT_ID /
CHALK_CLIENT_SECRET / CHALK_API_SERVER / CHALK_ENVIRONMENT_ID):

    ./evals.py smoke                 # one remote call, to check the model gateway
    ./evals.py run                   # upload both datasets, run both evaluations, save results

The agent reaches its model through the Chalk model gateway with the router key stored in the
Chalk secret CHALK_ROUTER_API_KEY (`chalk router api-key create`).
"""

from __future__ import annotations

import os

import chalkcompute
from chalkcompute import EvaluationScorerResult, Image, Secret

MODEL = os.environ.get("DISPUTE_AGENT_MODEL", "anthropic/claude-sonnet-5-5")

SYSTEM_PROMPT = """You are the dispute-review agent at Quillback Card, a card issuer.

A cardholder has opened a chargeback dispute. Decide one of:
- APPROVE: refund the cardholder now (the dispute looks legitimate).
- ESCALATE: hold the refund and send the case to the fraud team (it looks like first-party fraud:
  the cardholder disputing purchases they made or received).

You are given the dispute and the account context from the feature store: the current risk score
and its history, recent disputes, and the support thread. Every piece of evidence has an id in
square brackets. Base the decision on that context.

Reply with JSON only, no prose around it:
{"decision": "APPROVE" | "ESCALATE", "rationale": "<at most 50 words>", "evidence": ["<ids you relied on>"]}"""


@chalkcompute.function(
    image=Image.debian_slim(python_version="3.12").pip_install(["anthropic==1.13.0"]),
    cpu="1",
    memory="2Gi",
    concurrency=12,
    env={
        # The platform sets its own CHALK_API_SERVER inside the container, so the gateway URL
        # travels under a name of its own.
        "ROUTER_BASE_URL": os.environ.get("ROUTER_BASE_URL")
        or f"{os.environ.get('CHALK_API_SERVER', 'https://api.chalk.ai').rstrip('/')}/v1/router",
        "ROUTER_ENVIRONMENT_ID": os.environ.get("CHALK_ENVIRONMENT_ID", ""),
        "DISPUTE_AGENT_MODEL": MODEL,
        "ROUTER_API_KEY": Secret.from_chalk_env("CHALK_ROUTER_API_KEY"),
    },
)
def review_dispute(
    claim: str, amount: float, merchant: str, decision_time: str, context: str
) -> str:
    """The agent under evaluation: one model call through the Chalk model gateway."""
    import json

    import anthropic

    case = {
        "opened_at": decision_time,
        "merchant": merchant,
        "amount_usd": amount,
        "claim": claim,
    }
    client = anthropic.Anthropic(
        base_url=os.environ["ROUTER_BASE_URL"],
        api_key=os.environ["ROUTER_API_KEY"],
        default_headers={"X-Chalk-Env-Id": os.environ["ROUTER_ENVIRONMENT_ID"]},
    )
    message = client.messages.create(
        model=os.environ["DISPUTE_AGENT_MODEL"],
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": json.dumps(
                    {"dispute": case, **json.loads(context)}, indent=1
                ),
            }
        ],
    )
    text = "".join(
        block.text for block in message.content if block.type == "text"
    ).strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        answer = json.loads(text[start : end + 1])
    except ValueError:
        return json.dumps(
            {"decision": "UNPARSEABLE", "rationale": text, "evidence": []}
        )
    return json.dumps(
        {
            "decision": str(answer.get("decision", "")).upper(),
            "rationale": answer.get("rationale", ""),
            "evidence": [str(e) for e in answer.get("evidence", [])],
        }
    )


@chalkcompute.function(
    name="cites-only-past-evidence", cpu="1", memory="2Gi", concurrency=12
)
def cites_only_past_evidence(
    output: str, decision_time: str, evidence_times: str
) -> EvaluationScorerResult:
    """1.0 when every piece of evidence the agent cited existed when the dispute was opened.

    A rationale that leans on a later chargeback or the fraud team's finding scores 0.0: that
    evidence did not exist when the decision had to be made. The metadata names what it cited.
    """
    import json
    from datetime import datetime

    try:
        cited = [str(e) for e in json.loads(output)["evidence"]]
    except (ValueError, KeyError, TypeError):
        return EvaluationScorerResult(
            score=0.0, metadata={"error": "output is not the agent's JSON"}
        )
    happened = json.loads(evidence_times)
    decided = datetime.fromisoformat(decision_time)
    future = [
        e
        for e in cited
        if e in happened and datetime.fromisoformat(happened[e]) > decided
    ]
    return EvaluationScorerResult(
        score=0.0 if future else 1.0,
        metadata={"cited": cited, "cited_from_future": future},
    )


decision_matches_outcome = chalkcompute.scorers.json_match(
    inputs=("output", "expected"), paths=["decision"], name="decision-matches-outcome"
)


def main() -> None:
    import argparse
    import json
    import time
    from pathlib import Path

    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["smoke", "run"])
    args = parser.parse_args()

    datasets = {
        variant: [
            json.loads(line)
            for line in (here / "results" / "datasets" / f"{variant}.jsonl").open()
        ]
        for variant in ("point_in_time", "current_values")
    }

    if args.command == "smoke":
        row = datasets["point_in_time"][0]
        # Right after a redeploy the previous revision can still answer for a minute or two.
        for attempt in range(6):
            try:
                out = review_dispute.remote(
                    row["claim"],
                    row["amount"],
                    row["merchant"],
                    row["decision_time"],
                    row["context"],
                )
                print(row["case_id"], row["kind"], "->", out)
                return
            except Exception as exc:  # noqa: BLE001 - report and retry
                print(f"attempt {attempt + 1} failed: {str(exc)[-300:]}")
                time.sleep(30)
        raise SystemExit("smoke test failed")

    task_columns = ["claim", "amount", "merchant", "decision_time", "context"]
    scorer_columns = ["expected", "evidence_times"]
    keep = [
        "case_id",
        "account_id",
        "kind",
        "outcome",
        "features_as_of",
        *task_columns,
        *scorer_columns,
    ]
    summary: dict[str, dict] = {}
    runs = {}
    for variant, rows in datasets.items():
        dataset = chalkcompute.DatasetClient().upload(
            f"dispute-review-{variant.replace('_', '-')}",
            [{k: r[k] for k in keep} for r in rows],
        )
        evaluation = chalkcompute.Evaluation.create(
            f"dispute-review-{variant.replace('_', '-')}",
            dataset=dataset,
            task=review_dispute,
            scorers=[decision_matches_outcome, cites_only_past_evidence],
        )
        run = evaluation.run(metadata={"context": variant, "model": MODEL})
        runs[variant] = run
        summary[variant] = {"evaluation_id": evaluation.id, "evaluation_run_id": run.id}
        print(f"{variant}: evaluation {evaluation.id}, run {run.id}")

    # `run.wait()` gives up on a transient GetEvaluationRun timeout, so poll instead.
    pending = dict(runs)
    while pending:
        time.sleep(15)
        for variant, run in list(pending.items()):
            try:
                run = chalkcompute.EvaluationRun.from_id(run.id)
            except Exception as exc:  # noqa: BLE001 - a polling blip is not a failed run
                print(f"{variant}: poll failed ({exc}); retrying")
                continue
            if run.is_terminal:
                print(f"{variant}: {run.status}")
                runs[variant] = run
                del pending[variant]

    out_dir = here / "results"
    for variant, run in runs.items():
        summary[variant]["status"] = str(run.status)
        if run.result_dataset is None:
            continue
        table = chalkcompute.DatasetClient().read(run.result_dataset).to_pylist()
        (out_dir / f"eval_{variant}.jsonl").write_text(
            "".join(json.dumps(r, default=str) + "\n" for r in table)
        )
        for scorer in ("decision-matches-outcome", "cites-only-past-evidence"):
            values = [
                r.get(f"{scorer}_value")
                for r in table
                if r.get(f"{scorer}_value") is not None
            ]
            summary[variant][scorer] = (
                round(sum(values) / len(values), 4) if values else None
            )
    (out_dir / "evaluations.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
