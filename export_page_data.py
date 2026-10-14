#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Condense the recorded run in results/ into results/page_data.json for the demo page.

Everything in the output comes from files the other scripts wrote: the evaluation results
(`eval_*.jsonl`), the offline query snapshots (`timeline.json`), and the query and run ids
(`queries.json`, `evaluations.json`). The event text comes from the fictional history itself.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from disputes.history_data import ACCOUNTS, CASES, DISPUTES, MESSAGES, SCORE_UPDATES

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
FEATURED = ["CASE-0003", "CASE-0077", "CASE-0015", "CASE-0059"]
KIND_LABELS = {
    "latent_fraud": "Fraud that surfaced later",
    "visible_fraud": "Fraud visible at the time",
    "false_alarm": "False alarm",
    "legitimate": "Legitimate",
}
ID_RE = re.compile(r"^\[([A-Z]+-\d+)\]")


def load_eval(variant: str) -> dict[str, dict]:
    rows = [json.loads(line) for line in (RESULTS / f"eval_{variant}.jsonl").open()]
    out = {}
    for row in rows:
        answer = json.loads(row["output"])
        meta = json.loads(row.get("cites-only-past-evidence_metadata") or "{}")
        out[row["case_id"]] = {
            "decision": answer["decision"],
            "rationale": answer["rationale"],
            "evidence": answer["evidence"],
            "correct": row["decision-matches-outcome_value"] == 1.0,
            "onlyPastEvidence": row["cites-only-past-evidence_value"] == 1.0,
            "citedFromFuture": meta.get("cited_from_future", []),
        }
    return out


def account_events(account_id: str) -> list[dict]:
    events = []
    for rid, acct, score, reason, ts in SCORE_UPDATES:
        if acct == account_id:
            events.append(
                {
                    "id": rid,
                    "ts": ts.isoformat(),
                    "type": "score",
                    "text": f"Risk score {score:.2f}: {reason}",
                }
            )
    for rid, acct, amount, merchant, reason, ts in DISPUTES:
        if acct == account_id:
            events.append(
                {
                    "id": rid,
                    "ts": ts.isoformat(),
                    "type": "dispute",
                    "text": f"${amount:,.2f} at {merchant}: {reason}",
                }
            )
    for rid, acct, author, body, ts in MESSAGES:
        if acct == account_id:
            events.append(
                {
                    "id": rid,
                    "ts": ts.isoformat(),
                    "type": "message",
                    "author": author,
                    "text": body,
                }
            )
    return sorted(events, key=lambda e: e["ts"])


def visible_ids(snapshot: dict) -> list[str]:
    lines = (
        (snapshot["support_thread"] or [])
        + (snapshot["dispute_history"] or [])
        + (snapshot["risk_history"] or [])
    )
    return sorted({m.group(1) for line in lines if (m := ID_RE.match(line))})


def main() -> None:
    queries = json.loads((RESULTS / "queries.json").read_text())
    evaluations = json.loads((RESULTS / "evaluations.json").read_text())
    timeline = json.loads((RESULTS / "timeline.json").read_text())
    pit, cv = load_eval("point_in_time"), load_eval("current_values")
    holders = {a[0]: a[1] for a in ACCOUNTS}

    cases = []
    for case_id, account_id, amount, merchant, claim, opened_at, kind, outcome in CASES:
        cases.append(
            {
                "id": case_id,
                "accountId": account_id,
                "kind": kind,
                "outcome": outcome,
                "openedAt": opened_at.isoformat(),
                "amount": amount,
                "merchant": merchant,
                "claim": claim,
                "pointInTime": {
                    k: pit[case_id][k]
                    for k in ("decision", "correct", "onlyPastEvidence")
                },
                "currentValues": {
                    k: cv[case_id][k]
                    for k in ("decision", "correct", "onlyPastEvidence")
                },
            }
        )

    by_kind = []
    for kind, label in KIND_LABELS.items():
        rows = [c for c in cases if c["kind"] == kind]
        by_kind.append(
            {
                "kind": kind,
                "label": label,
                "cases": len(rows),
                "pointInTimeCorrect": sum(c["pointInTime"]["correct"] for c in rows),
                "currentValuesCorrect": sum(
                    c["currentValues"]["correct"] for c in rows
                ),
            }
        )

    def run_summary(variant: str, answers: dict[str, dict]) -> dict:
        n = len(answers)
        return {
            "evaluationId": evaluations[variant]["evaluation_id"],
            "evaluationRunId": evaluations[variant]["evaluation_run_id"],
            "offlineQueryDatasetId": queries[variant]["offline_query_dataset_id"],
            "cases": n,
            "decisionMatchesOutcome": round(
                sum(a["correct"] for a in answers.values()) / n, 4
            ),
            "citesOnlyPastEvidence": round(
                sum(a["onlyPastEvidence"] for a in answers.values()) / n, 4
            ),
        }

    featured = []
    for case_id in FEATURED:
        case = next(c for c in cases if c["id"] == case_id)
        recorded = timeline["cases"][case_id]
        featured.append(
            {
                **{
                    k: case[k]
                    for k in (
                        "id",
                        "accountId",
                        "kind",
                        "outcome",
                        "openedAt",
                        "amount",
                        "merchant",
                        "claim",
                    )
                },
                "kindLabel": KIND_LABELS[case["kind"]],
                "holder": holders[case["accountId"]],
                "timelineQueryDatasetId": recorded["offline_query_dataset_id"],
                "events": account_events(case["accountId"]),
                "snapshots": [
                    {
                        "asOf": s["as_of"],
                        "riskScore": s["risk_score"],
                        "riskReason": s["risk_score_reason"],
                        "disputes30d": s["disputes_30d"],
                        "disputes90d": s["disputes_90d"],
                        "visibleEventIds": visible_ids(s),
                    }
                    for s in recorded["snapshots"]
                ],
                "pointInTime": pit[case_id],
                "currentValues": cv[case_id],
            }
        )

    page = {
        "recordedAt": queries["run_at"],
        "model": "anthropic/claude-sonnet-5-5",
        "runs": {
            "pointInTime": run_summary("point_in_time", pit),
            "currentValues": run_summary("current_values", cv),
        },
        "byKind": by_kind,
        "cases": cases,
        "featured": featured,
    }
    out = RESULTS / "page_data.json"
    out.write_text(json.dumps(page, indent=1) + "\n")
    print(f"wrote {out}: {json.dumps(page['runs'], indent=1)}")
    for k in by_kind:
        print(k)


if __name__ == "__main__":
    main()
