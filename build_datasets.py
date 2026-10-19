#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkpy[runtime]==2.155.2"]
# ///
"""Build the two evaluation datasets from Chalk offline queries.

Both datasets hold the same 36 historical disputes and the same ground truth. They differ only in
the context the agent will see:

- point_in_time: every case is queried with `input_times` set to the moment the dispute was opened,
  so the features are what the reviewing agent could actually have seen.
- current_values: every case is queried as of now, which is what a replay built on "today's"
  feature table gives you. It includes everything that happened after the decision.

    ./build_datasets.py [--branch time-correct-evals]

Credentials come from your existing `chalk login` (or CHALK_CLIENT_ID / CHALK_CLIENT_SECRET / CHALK_API_SERVER /
CHALK_ENVIRONMENT_ID). Writes results/datasets/*.jsonl and results/queries.json.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"


def chalk_client(branch: str):
    from chalk.client import ChalkClient

    return ChalkClient(branch=branch)


def query(
    client, case_ids: list[str], times: list[datetime], name: str
) -> tuple[str, list[dict]]:
    from disputes.features import DisputeCase

    acct = DisputeCase.account
    ds = client.offline_query(
        input={DisputeCase.id: case_ids},
        input_times=times,
        output=[
            DisputeCase.id,
            DisputeCase.account_id,
            DisputeCase.amount,
            DisputeCase.merchant,
            DisputeCase.claim,
            DisputeCase.opened_at,
            DisputeCase.as_of,
            acct.holder,
            acct.risk_score,
            acct.risk_score_reason,
            acct.risk_history,
            acct.disputes_filed["30d"],
            acct.disputes_filed["90d"],
            acct.disputes_filed["365d"],
            acct.dispute_history,
            acct.support_thread,
        ],
        recompute_features=True,
        run_asynchronously=False,
        dataset_name=name,
        show_progress=False,
    )
    frame = ds.get_data_as_polars()
    frame = frame.collect() if hasattr(frame, "collect") else frame
    return str(ds.dataset_id), frame.to_dicts()


def strip_prefix(row: dict) -> dict:
    """`dispute_case.account.disputes_filed__7776000__` -> `account.disputes_filed_90d`."""
    windows = {"2592000": "30d", "7776000": "90d", "31536000": "365d"}
    out = {}
    for key, value in row.items():
        key = key.removeprefix("dispute_case.")
        for secs, label in windows.items():
            key = key.replace(f"__{secs}__", f"_{label}")
        out[key] = value
    return out


def to_eval_row(case: tuple, features: dict, evidence_times: dict[str, str]) -> dict:
    case_id, account_id, amount, merchant, claim, opened_at, kind, outcome = case
    context = {
        "account": {
            "id": account_id,
            "holder": features["account.holder"],
            "risk_score": features["account.risk_score"],
            "risk_score_reason": features["account.risk_score_reason"],
            "disputes_filed_30d": features["account.disputes_filed_30d"],
            "disputes_filed_90d": features["account.disputes_filed_90d"],
            "disputes_filed_365d": features["account.disputes_filed_365d"],
            "risk_history": features["account.risk_history"],
            "dispute_history": features["account.dispute_history"],
            "support_thread": features["account.support_thread"],
        }
    }
    expected_decision = "ESCALATE" if outcome == "first_party_fraud" else "APPROVE"
    return {
        "case_id": case_id,
        "account_id": account_id,
        "kind": kind,
        "outcome": outcome,
        "decision_time": opened_at.isoformat(),
        "claim": claim,
        "amount": float(amount),
        "merchant": merchant,
        "context": json.dumps(context),
        "expected": json.dumps({"decision": expected_decision}),
        "evidence_times": json.dumps(evidence_times),
        # `as_of` is `_.chalk_now` echoed back by Chalk: the time these features were computed for.
        "features_as_of": features["as_of"].isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", default="time-correct-evals")
    args = parser.parse_args()

    from disputes.history_data import CASES, DISPUTES, MESSAGES, SCORE_UPDATES

    # When every piece of evidence happened, for the scorer that flags rationales citing the future.
    evidence: dict[str, dict[str, str]] = {}
    for rid, account_id, *_rest, ts in SCORE_UPDATES:
        evidence.setdefault(account_id, {})[rid] = ts.isoformat()
    for rid, account_id, *_rest, ts in DISPUTES:
        evidence.setdefault(account_id, {})[rid] = ts.isoformat()
    for rid, account_id, *_rest, ts in MESSAGES:
        evidence.setdefault(account_id, {})[rid] = ts.isoformat()

    client = chalk_client(args.branch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    case_ids = [c[0] for c in CASES]
    variants = {
        "point_in_time": [c[5] for c in CASES],
        "current_values": [now] * len(CASES),
    }
    (OUT / "datasets").mkdir(parents=True, exist_ok=True)
    queries = {"branch": args.branch, "run_at": now.isoformat()}
    by_id = {c[0]: c for c in CASES}
    for variant, times in variants.items():
        dataset_id, rows = query(client, case_ids, times, f"dispute-review-{variant}")
        features = {r["id"]: r for r in map(strip_prefix, rows)}
        eval_rows = [
            to_eval_row(by_id[cid], features[cid], evidence[by_id[cid][1]])
            for cid in case_ids
        ]
        path = OUT / "datasets" / f"{variant}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in eval_rows))
        queries[variant] = {
            "offline_query_dataset_id": dataset_id,
            "rows": len(eval_rows),
        }
        print(
            f"{variant}: offline query dataset {dataset_id}, {len(eval_rows)} rows -> {path}"
        )
    (OUT / "queries.json").write_text(json.dumps(queries, indent=2) + "\n")


if __name__ == "__main__":
    main()
