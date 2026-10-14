"""Resolvers over the fictional history in `history_data.py`.

The three history resolvers return every row ever recorded, each stamped with when it happened. They
do no time filtering themselves: the `_.chalk_now` filters on the aggregations in `features.py` do
that, so the same resolver serves today's values and any point in the past. In a real deployment
these would be SQL resolvers over the event tables.
"""

from __future__ import annotations

from datetime import datetime

from chalk import DataFrame, online
from chalk.features import Features

from disputes.features import Account, Dispute, DisputeCase, ScoreUpdate, TicketMessage
from disputes.history_data import ACCOUNTS, CASES, DISPUTES, MESSAGES, SCORE_UPDATES

_ACCOUNTS = {row[0]: row for row in ACCOUNTS}
_CASES = {row[0]: row for row in CASES}


@online
def get_account(id: Account.id) -> Features[Account.holder, Account.opened_at]:
    _, holder, opened_at = _ACCOUNTS[id]
    return Account(holder=holder, opened_at=opened_at)


@online
def get_case(
    id: DisputeCase.id,
) -> Features[
    DisputeCase.account_id,
    DisputeCase.amount,
    DisputeCase.merchant,
    DisputeCase.claim,
    DisputeCase.opened_at,
]:
    _, account_id, amount, merchant, claim, opened_at, _kind, _outcome = _CASES[id]
    return DisputeCase(
        account_id=account_id,
        amount=amount,
        merchant=merchant,
        claim=claim,
        opened_at=opened_at,
    )


def _day(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d")


@online
def all_score_updates() -> DataFrame[
    ScoreUpdate.id,
    ScoreUpdate.account_id,
    ScoreUpdate.score,
    ScoreUpdate.reason,
    ScoreUpdate.ts,
    ScoreUpdate.line,
]:
    return DataFrame(
        [
            ScoreUpdate(
                id=sid,
                account_id=account_id,
                score=score,
                reason=reason,
                ts=ts,
                line=f"[{sid}] {_day(ts)} risk {score:.2f} ({reason})",
            )
            for sid, account_id, score, reason, ts in SCORE_UPDATES
        ]
    )


@online
def all_disputes() -> DataFrame[
    Dispute.id,
    Dispute.account_id,
    Dispute.amount,
    Dispute.merchant,
    Dispute.reason,
    Dispute.opened_at,
    Dispute.line,
]:
    return DataFrame(
        [
            Dispute(
                id=did,
                account_id=account_id,
                amount=amount,
                merchant=merchant,
                reason=reason,
                opened_at=opened_at,
                line=f"[{did}] {_day(opened_at)} ${amount:,.2f} at {merchant}: {reason}",
            )
            for did, account_id, amount, merchant, reason, opened_at in DISPUTES
        ]
    )


@online
def all_messages() -> DataFrame[
    TicketMessage.id,
    TicketMessage.account_id,
    TicketMessage.author,
    TicketMessage.body,
    TicketMessage.sent_at,
    TicketMessage.line,
]:
    return DataFrame(
        [
            TicketMessage(
                id=mid,
                account_id=account_id,
                author=author,
                body=body,
                sent_at=sent_at,
                line=f"[{mid}] {sent_at.strftime('%Y-%m-%d %H:%M')} {author}: {body}",
            )
            for mid, account_id, author, body, sent_at in MESSAGES
        ]
    )
