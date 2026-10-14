#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkpy[runtime]==2.155.2"]
# ///
"""Record the account features for a few cases at weekly points in time.

One offline query per case: the same DisputeCase id repeated, with `input_times` stepping weekly
from three weeks before the dispute to today, plus the exact moment the dispute was opened. The
demo page's timeline scrubs through these snapshots.

    ./record_timeline.py CASE-0003 CASE-0081 ...
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_datasets import chalk_client, strip_prefix

HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_ids", nargs="+")
    parser.add_argument("--branch", default="time-correct-evals")
    args = parser.parse_args()

    from disputes.features import DisputeCase
    from disputes.history_data import CASES

    by_id = {c[0]: c for c in CASES}
    client = chalk_client(args.branch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    acct = DisputeCase.account
    out = {"recorded_at": now.isoformat(), "branch": args.branch, "cases": {}}
    for case_id in args.case_ids:
        opened_at = by_id[case_id][5]
        times = []
        t = opened_at - timedelta(days=21)
        while t < now:
            times.append(t)
            t += timedelta(days=7)
        times = sorted({*times, opened_at, now})
        ds = client.offline_query(
            input={DisputeCase.id: [case_id] * len(times)},
            input_times=times,
            output=[
                DisputeCase.id,
                DisputeCase.as_of,
                acct.risk_score,
                acct.risk_score_reason,
                acct.disputes_filed["30d"],
                acct.disputes_filed["90d"],
                acct.support_thread,
                acct.dispute_history,
                acct.risk_history,
            ],
            recompute_features=True,
            run_asynchronously=False,
            dataset_name=f"dispute-review-timeline-{case_id.lower()}",
            show_progress=False,
        )
        frame = ds.get_data_as_polars()
        frame = frame.collect() if hasattr(frame, "collect") else frame
        rows = sorted(
            (strip_prefix(r) for r in frame.to_dicts()), key=lambda r: r["as_of"]
        )
        snapshots = [
            {
                # `as_of` is `_.chalk_now` echoed back: the point in time this row was computed at.
                "as_of": r["as_of"].isoformat(),
                "risk_score": r["account.risk_score"],
                "risk_score_reason": r["account.risk_score_reason"],
                "disputes_30d": r["account.disputes_filed_30d"],
                "disputes_90d": r["account.disputes_filed_90d"],
                "support_thread": r["account.support_thread"],
                "dispute_history": r["account.dispute_history"],
                "risk_history": r["account.risk_history"],
            }
            for r in rows
        ]
        assert [s["as_of"] for s in snapshots] == [t.isoformat() for t in times], (
            "missing snapshots"
        )
        out["cases"][case_id] = {
            "offline_query_dataset_id": str(ds.dataset_id),
            "opened_at": opened_at.isoformat(),
            "snapshots": snapshots,
        }
        print(
            f"{case_id}: {len(snapshots)} snapshots, offline query dataset {ds.dataset_id}"
        )
    path = HERE / "results" / "timeline.json"
    path.write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
