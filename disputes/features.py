"""Features for Quillback Card's dispute-review agent.

Every history row carries the time it happened (a FeatureTime, or a timestamp the aggregations
filter on), and every aggregation filters on `_.chalk_now`. `chalk_now` is the query's notion of
"now": the wall clock for an online query, and each row's `input_times` entry for an offline query.
That is what makes an offline query return the account exactly as it looked when the dispute was
opened, instead of as it looks today.
"""

from __future__ import annotations

from datetime import datetime

import chalk.functions as F
from chalk import DataFrame, Windowed, windowed
from chalk.features import FeatureTime, _, features, has_many, has_one


@features
class ScoreUpdate:
    """One output of the risk model. The score is refreshed monthly and on risk events."""

    id: str
    account_id: str
    score: float
    reason: str
    ts: FeatureTime
    line: str


@features
class Dispute:
    """Every dispute an account has filed, including the one under review."""

    id: str
    account_id: str
    amount: float
    merchant: str
    reason: str
    opened_at: FeatureTime
    line: str


@features
class TicketMessage:
    """The support thread: customer, support, merchant and fraud-team messages."""

    id: str
    account_id: str
    author: str
    body: str
    sent_at: FeatureTime
    line: str


@features
class Account:
    id: str
    holder: str
    opened_at: datetime

    score_updates: DataFrame[ScoreUpdate] = has_many(
        lambda: ScoreUpdate.account_id == Account.id
    )
    disputes: DataFrame[Dispute] = has_many(lambda: Dispute.account_id == Account.id)
    messages: DataFrame[TicketMessage] = has_many(
        lambda: TicketMessage.account_id == Account.id
    )

    # The latest risk score as of the query time, not the latest one ever written.
    risk_score: float = F.max_by(_.score_updates[_.score, _.ts <= _.chalk_now], _.ts)
    risk_score_reason: str = F.max_by(
        _.score_updates[_.reason, _.ts <= _.chalk_now], _.ts
    )
    risk_history: list[str] = F.max_by_n(
        _.score_updates[_.line, _.ts <= _.chalk_now], _.ts, 6
    )

    disputes_filed: Windowed[int] = windowed(
        "30d",
        "90d",
        "365d",
        expression=_.disputes[
            _.opened_at > _.chalk_window, _.opened_at <= _.chalk_now
        ].count(),
        default=0,
    )
    dispute_history: list[str] = F.max_by_n(
        _.disputes[_.line, _.opened_at <= _.chalk_now], _.opened_at, 8
    )

    # The support thread as it read at the query time, newest first.
    support_thread: list[str] = F.max_by_n(
        _.messages[_.line, _.sent_at <= _.chalk_now], _.sent_at, 12
    )


@features
class DisputeCase:
    """A dispute under review: the agent decides APPROVE (refund) or ESCALATE (fraud team)."""

    id: str
    account_id: str
    amount: float
    merchant: str
    claim: str
    opened_at: datetime
    account: Account = has_one(lambda: Account.id == DisputeCase.account_id)
    # The time the features were evaluated at, so each output row says which point in time it is.
    as_of: datetime = _.chalk_now
