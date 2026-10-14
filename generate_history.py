#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Generate the fictional dispute history in `disputes/history_data.py`.

Quillback Card is a made-up card issuer. Every account files a chargeback dispute ("I didn't make
this purchase", "never arrived", ...) and a review agent decides APPROVE (refund the customer) or
ESCALATE (send to the fraud team). The ground truth is what the fraud team concluded weeks later.

Four kinds of cases, chosen so that the information available at decision time and the information
available today disagree in realistic ways:

- visible fraud: a serial disputer whose history already looked bad when the dispute was opened.
- latent fraud: a clean-looking first dispute; the follow-up disputes, the merchant's signed proof of
  delivery and the fraud team's finding all arrive *after* the decision.
- legitimate: a clean account with an ordinary billing problem.
- false alarm: an account whose risk score was elevated at the time (new device, address change,
  an earlier dispute), later cleared when the merchant admitted a billing error.

Run it again to regenerate; the seed makes the output byte-for-byte stable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20261012
OUT = Path(__file__).resolve().parent / "disputes" / "history_data.py"
UTC = timezone.utc

FIRST = [
    "Avery",
    "Jordan",
    "Riley",
    "Morgan",
    "Casey",
    "Quinn",
    "Rowan",
    "Emerson",
    "Hayden",
    "Parker",
    "Sawyer",
    "Reese",
    "Dakota",
    "Finley",
    "Harper",
    "Kendall",
    "Logan",
    "Marlow",
    "Noel",
    "Peyton",
    "Remy",
    "Sage",
    "Tatum",
    "Wren",
    "Ellis",
    "Blair",
    "Camden",
    "Devon",
    "Greer",
    "Jules",
    "Linden",
    "Oakley",
    "Shea",
    "Teagan",
    "Vale",
    "Arden",
]
LAST = [
    "Okafor",
    "Lindqvist",
    "Marchetti",
    "Haddad",
    "Nakamura",
    "Brennan",
    "Castellanos",
    "Duvall",
    "Ferreira",
    "Gallagher",
    "Ivanova",
    "Kowalczyk",
    "Moreau",
    "Ndiaye",
    "Petrov",
    "Rasmussen",
    "Salazar",
    "Takahashi",
    "Underwood",
    "Vasquez",
    "Whitfield",
    "Yilmaz",
    "Abernathy",
    "Bjornsen",
    "Cardenas",
    "Delacroix",
    "Esposito",
    "Fairbanks",
    "Grimaldi",
    "Holloway",
    "Iversen",
    "Jablonski",
    "Kaminski",
    "Laurent",
    "Mbeki",
    "Novak",
]
MERCHANTS = {
    "electronics": ["Voltline Electronics", "Pixelridge Store", "Northgate Audio"],
    "travel": ["Skylark Air", "Harborview Hotels", "Meridian Rail"],
    "apparel": ["Fernwood Outfitters", "Loomhouse", "Cobalt Footwear"],
    "home": ["Hearthstone Home", "Birchline Furniture", "Kiln & Co"],
    "subscriptions": ["Streamwell", "Cadence Fitness", "Inkpot Books"],
}

# The data describes the past relative to this instant; nothing is dated after it.
HISTORY_END = datetime(2026, 10, 10, tzinfo=UTC)


@dataclass
class Account:
    id: str
    holder: str
    opened_at: datetime
    kind: str
    score_updates: list[tuple[str, float, str, datetime]] = field(default_factory=list)
    disputes: list[tuple[str, float, str, str, datetime]] = field(default_factory=list)
    messages: list[tuple[str, str, str, datetime]] = field(default_factory=list)


@dataclass
class Case:
    id: str
    account_id: str
    amount: float
    merchant: str
    claim: str
    opened_at: datetime
    kind: str
    outcome: (
        str  # "first_party_fraud" or "legitimate", as concluded by the fraud team later
    )


def at(day: datetime, hour: int, minute: int) -> datetime:
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


class Builder:
    def __init__(self) -> None:
        super().__init__()
        self.rng = random.Random(SEED)
        self.accounts: list[Account] = []
        self.cases: list[Case] = []
        self.n_msg = 0
        self.n_score = 0
        self.n_dispute = 0

    # -- event helpers ---------------------------------------------------------------------
    def score(self, acct: Account, value: float, reason: str, ts: datetime) -> None:
        self.n_score += 1
        acct.score_updates.append(
            (f"SCR-{self.n_score:04d}", round(value, 2), reason, ts)
        )

    def msg(self, acct: Account, author: str, body: str, ts: datetime) -> None:
        self.n_msg += 1
        acct.messages.append((f"MSG-{self.n_msg:04d}", author, body, ts))

    def dispute(
        self, acct: Account, amount: float, merchant: str, reason: str, ts: datetime
    ) -> str:
        self.n_dispute += 1
        did = f"DSP-{self.n_dispute:04d}"
        acct.disputes.append((did, round(amount, 2), merchant, reason, ts))
        return did

    def monthly_scores(
        self, acct: Account, start: datetime, end: datetime, base: float, jitter: float
    ) -> None:
        """A model refresh on the 1st of every month, with small noise around `base`."""
        day = datetime(start.year, start.month, 1, tzinfo=UTC)
        while day <= end:
            if day >= start:
                value = min(0.99, max(0.01, base + self.rng.uniform(-jitter, jitter)))
                self.score(acct, value, "monthly model refresh", at(day, 4, 0))
            day = (day + timedelta(days=32)).replace(day=1)

    def merchant(self) -> tuple[str, str]:
        category = self.rng.choice(sorted(MERCHANTS))
        return category, self.rng.choice(MERCHANTS[category])

    def new_account(self, kind: str) -> Account:
        n = len(self.accounts)
        holder = f"{FIRST[n % len(FIRST)]} {LAST[(n * 7) % len(LAST)]}"
        opened = datetime(
            2019 + self.rng.randrange(6),
            1 + self.rng.randrange(12),
            1 + self.rng.randrange(27),
            tzinfo=UTC,
        )
        acct = Account(id=f"QB-{1001 + n}", holder=holder, opened_at=opened, kind=kind)
        self.accounts.append(acct)
        return acct

    def open_case(
        self,
        acct: Account,
        amount: float,
        merchant: str,
        claim: str,
        ts: datetime,
        kind: str,
        outcome: str,
    ) -> Case:
        did = self.dispute(acct, amount, merchant, claim, ts)
        case = Case(
            id=did.replace("DSP", "CASE"),
            account_id=acct.id,
            amount=round(amount, 2),
            merchant=merchant,
            claim=claim,
            opened_at=ts,
            kind=kind,
            outcome=outcome,
        )
        self.cases.append(case)
        return case

    def decision_day(self) -> datetime:
        return datetime(2026, 5, 4, tzinfo=UTC) + timedelta(
            days=self.rng.randrange(0, 115)
        )

    # -- case recipes ----------------------------------------------------------------------
    def visible_fraud(
        self, acct: Account | None = None, day: datetime | None = None
    ) -> Case:
        acct = acct or self.new_account("visible_fraud")
        day = day or self.decision_day()
        base = self.rng.uniform(0.62, 0.78)
        self.monthly_scores(
            acct, datetime(2026, 3, 1, tzinfo=UTC), day, base - 0.12, 0.04
        )
        for i, back in enumerate(
            sorted(self.rng.sample(range(12, 70), 2), reverse=True)
        ):
            _, m = self.merchant()
            amount = self.rng.uniform(80, 600)
            reason = self.rng.choice(["item not received", "unauthorized charge"])
            d = self.dispute(
                acct, amount, m, reason, at(day - timedelta(days=back), 15, 10 + i)
            )
            self.msg(
                acct,
                "customer",
                f"Filing another dispute ({d}) for {m}, ${amount:,.2f}. {reason.capitalize()}.",
                at(day - timedelta(days=back), 15, 12 + i),
            )
        self.score(
            acct,
            base,
            "dispute velocity: 2 disputes in 70 days",
            at(day - timedelta(days=9), 6, 0),
        )
        self.msg(
            acct,
            "support",
            "Note: customer's third chargeback request this quarter; prior two claimed "
            "non-delivery on orders the carrier marked delivered.",
            at(day - timedelta(days=8), 11, 40),
        )
        _, m = self.merchant()
        amount = self.rng.uniform(450, 1900)
        claim = self.rng.choice(
            [
                "I never received this order.",
                "I did not authorize this purchase.",
                "The package never showed up.",
            ]
        )
        case = self.open_case(
            acct,
            amount,
            m,
            claim,
            at(day, 14, self.rng.randrange(0, 59)),
            "visible_fraud",
            "first_party_fraud",
        )
        self.msg(
            acct,
            "customer",
            f"{claim} It's from {m}, ${amount:,.2f}. I want my money back today.",
            case.opened_at - timedelta(minutes=2),
        )
        self.after_fraud_confirmed(acct, case)
        return case

    def latent_fraud(
        self,
        acct: Account | None = None,
        day: datetime | None = None,
        history_from: datetime | None = None,
    ) -> Case:
        acct = acct or self.new_account("latent_fraud")
        day = day or self.decision_day()
        base = self.rng.uniform(0.09, 0.2)
        self.monthly_scores(
            acct, history_from or datetime(2026, 3, 1, tzinfo=UTC), day, base, 0.03
        )
        _, m = self.merchant()
        amount = self.rng.uniform(600, 2200)
        claim = self.rng.choice(
            [
                "I don't recognize this charge.",
                "I never received this order.",
                "This purchase wasn't made by me.",
            ]
        )
        case = self.open_case(
            acct,
            amount,
            m,
            claim,
            at(day, 10, self.rng.randrange(0, 59)),
            "latent_fraud",
            "first_party_fraud",
        )
        self.msg(
            acct,
            "customer",
            f"Hi, {claim if claim.startswith('I ') else claim[0].lower() + claim[1:]} {m}, ${amount:,.2f}. Could you look into it? "
            "Thanks so much.",
            case.opened_at - timedelta(minutes=2),
        )
        # Everything below happens after the decision: more disputes, the merchant's evidence,
        # and the fraud team's finding.
        for i, after in enumerate(sorted(self.rng.sample(range(4, 26), 3))):
            _, m2 = self.merchant()
            amt2 = self.rng.uniform(300, 1400)
            d = self.dispute(
                acct,
                amt2,
                m2,
                "item not received",
                at(day + timedelta(days=after), 16, 5 + i),
            )
            self.msg(
                acct,
                "customer",
                f"Another order didn't arrive ({d}): {m2}, ${amt2:,.2f}.",
                at(day + timedelta(days=after), 16, 7 + i),
            )
        self.score(
            acct,
            self.rng.uniform(0.55, 0.68),
            "dispute velocity: 4 disputes in 26 days",
            at(day + timedelta(days=27), 6, 0),
        )
        self.after_fraud_confirmed(acct, case, delay=self.rng.randrange(29, 44))
        return case

    def after_fraud_confirmed(
        self, acct: Account, case: Case, delay: int | None = None
    ) -> None:
        delay = delay or self.rng.randrange(10, 30)
        t = case.opened_at + timedelta(days=delay)
        self.msg(
            acct,
            "merchant",
            f"{case.merchant} evidence for {case.id.replace('CASE', 'DSP')}: signed delivery "
            "confirmation, and the order was placed from the cardholder's usual device and IP.",
            at(t - timedelta(days=2), 9, 30),
        )
        self.msg(
            acct,
            "fraud-team",
            f"Investigation closed: confirmed first-party fraud on {case.id}. "
            "Account restricted and refund clawed back.",
            at(t, 17, 45),
        )
        self.score(
            acct,
            self.rng.uniform(0.88, 0.97),
            "fraud team confirmed first-party fraud",
            at(t, 18, 0),
        )

    def legitimate(
        self,
        acct: Account | None = None,
        day: datetime | None = None,
        history_from: datetime | None = None,
        through: datetime | None = None,
    ) -> Case:
        acct = acct or self.new_account("legitimate")
        day = day or self.decision_day()
        base = self.rng.uniform(0.04, 0.16)
        self.monthly_scores(
            acct,
            history_from or datetime(2026, 3, 1, tzinfo=UTC),
            through or HISTORY_END,
            base,
            0.03,
        )
        if self.rng.random() < 0.3:
            _, m0 = self.merchant()
            self.dispute(
                acct,
                self.rng.uniform(20, 90),
                m0,
                "duplicate charge",
                at(day - timedelta(days=self.rng.randrange(150, 300)), 12, 0),
            )
        _, m = self.merchant()
        amount = self.rng.uniform(40, 700)
        order = self.rng.randrange(10000, 99999)
        claim, detail = self.rng.choice(
            [
                (
                    "I was charged twice for the same order.",
                    f"Order #{order} shows two identical charges two minutes apart.",
                ),
                (
                    "I cancelled and was still billed.",
                    f"I cancelled on the website and have the confirmation email for #{order}.",
                ),
                (
                    "I returned the item and haven't been refunded.",
                    f"Return tracking for #{order} shows it was delivered back to them.",
                ),
            ]
        )
        case = self.open_case(
            acct,
            amount,
            m,
            claim,
            at(day, 9, self.rng.randrange(0, 59)),
            "legitimate",
            "legitimate",
        )
        self.msg(
            acct,
            "customer",
            f"{claim} {detail} Merchant: {m}, ${amount:,.2f}.",
            case.opened_at - timedelta(minutes=2),
        )
        t = day + timedelta(days=self.rng.randrange(5, 15))
        self.msg(
            acct,
            "merchant",
            f"{m}: confirmed the billing error on {case.id.replace('CASE', 'DSP')} and issued a "
            "refund.",
            at(t, 13, 20),
        )
        return case

    def false_alarm(
        self, acct: Account | None = None, day: datetime | None = None
    ) -> Case:
        acct = acct or self.new_account("false_alarm")
        day = day or self.decision_day()
        self.monthly_scores(
            acct, datetime(2026, 3, 1, tzinfo=UTC), day - timedelta(days=20), 0.14, 0.03
        )
        self.msg(
            acct,
            "system",
            "New device enrolled and mailing address changed on the same day.",
            at(day - timedelta(days=18), 20, 15),
        )
        self.score(
            acct,
            self.rng.uniform(0.58, 0.7),
            "new device + address change within 24h",
            at(day - timedelta(days=17), 6, 0),
        )
        _, m0 = self.merchant()
        back = self.rng.randrange(35, 70)
        self.dispute(
            acct,
            self.rng.uniform(60, 300),
            m0,
            "unauthorized charge",
            at(day - timedelta(days=back), 18, 0),
        )
        _, m = self.merchant()
        amount = self.rng.uniform(250, 1200)
        claim = "I was charged for something I cancelled."
        case = self.open_case(
            acct,
            amount,
            m,
            claim,
            at(day, 19, self.rng.randrange(0, 59)),
            "false_alarm",
            "legitimate",
        )
        self.msg(
            acct,
            "customer",
            f"This is the SECOND time {m} has done this!! ${amount:,.2f} for something I "
            "cancelled weeks ago. Fix it now.",
            case.opened_at - timedelta(minutes=2),
        )
        t = day + timedelta(days=self.rng.randrange(3, 12))
        self.msg(
            acct,
            "support",
            "Identity verified on a recorded call; customer moved house and replaced their "
            "phone, which explains the device and address change.",
            at(t, 10, 5),
        )
        self.msg(
            acct,
            "merchant",
            f"{m}: subscription cancellation was not processed on our side. Refund issued for "
            f"{case.id.replace('CASE', 'DSP')}.",
            at(t + timedelta(days=2), 14, 0),
        )
        self.score(
            acct,
            self.rng.uniform(0.08, 0.15),
            "risk cleared after identity verification",
            at(t + timedelta(days=3), 6, 0),
        )
        self.monthly_scores(acct, t + timedelta(days=10), HISTORY_END, 0.12, 0.03)
        return case

    def featured(self) -> None:
        """QB-1001: the account the page's timeline scrubs through. A routine refund in June,
        then a first dispute in August that only later proves to be first-party fraud."""
        acct = self.new_account("featured")
        june = datetime(2026, 6, 9, tzinfo=UTC)
        aug = datetime(2026, 8, 14, tzinfo=UTC)
        self.legitimate(
            acct,
            june,
            history_from=datetime(2026, 3, 1, tzinfo=UTC),
            through=aug - timedelta(days=1),
        )
        self.latent_fraud(acct, aug, history_from=aug)

    def build(self) -> None:
        self.featured()
        for kind, n in (
            ("visible_fraud", 7),
            ("latent_fraud", 6),
            ("legitimate", 16),
            ("false_alarm", 5),
        ):
            for _ in range(n):
                getattr(self, kind)()
        for acct in self.accounts:
            acct.score_updates.sort(key=lambda r: r[3])
            acct.messages.sort(key=lambda r: r[3])
            acct.disputes.sort(key=lambda r: r[4])
            for rows in (acct.score_updates, acct.messages, acct.disputes):
                assert all(r[-1] <= HISTORY_END for r in rows), (acct.id, rows)


def fmt(ts: datetime) -> str:
    return (
        f"datetime({ts.year}, {ts.month}, {ts.day}, {ts.hour}, {ts.minute}, tzinfo=UTC)"
    )


def render(b: Builder) -> str:
    lines = [
        '"""Generated by generate_history.py. Do not edit by hand."""',
        "",
        "from datetime import datetime, timezone",
        "",
        "UTC = timezone.utc",
        "",
        "# (id, holder, opened_at)",
        "ACCOUNTS = [",
    ]
    lines += [f"    ({a.id!r}, {a.holder!r}, {fmt(a.opened_at)})," for a in b.accounts]
    lines += ["]", "", "# (id, account_id, score, reason, ts)", "SCORE_UPDATES = ["]
    lines += [
        f"    ({sid!r}, {a.id!r}, {v!r}, {r!r}, {fmt(ts)}),"
        for a in b.accounts
        for sid, v, r, ts in a.score_updates
    ]
    lines += [
        "]",
        "",
        "# (id, account_id, amount, merchant, reason, opened_at)",
        "DISPUTES = [",
    ]
    lines += [
        f"    ({did!r}, {a.id!r}, {amt!r}, {m!r}, {r!r}, {fmt(ts)}),"
        for a in b.accounts
        for did, amt, m, r, ts in a.disputes
    ]
    lines += ["]", "", "# (id, account_id, author, body, sent_at)", "MESSAGES = ["]
    lines += [
        f"    ({mid!r}, {a.id!r}, {au!r}, {body!r}, {fmt(ts)}),"
        for a in b.accounts
        for mid, au, body, ts in a.messages
    ]
    lines += [
        "]",
        "",
        "# (id, account_id, amount, merchant, claim, opened_at, kind, outcome)",
        "CASES = [",
    ]
    lines += [
        f"    ({c.id!r}, {c.account_id!r}, {c.amount!r}, {c.merchant!r}, {c.claim!r}, {fmt(c.opened_at)}, "
        f"{c.kind!r}, {c.outcome!r}),"
        for c in sorted(b.cases, key=lambda c: c.opened_at)
    ]
    lines += ["]", ""]
    return "\n".join(lines)


def main() -> None:
    b = Builder()
    b.build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(b))
    kinds: dict[str, int] = {}
    for c in b.cases:
        kinds[c.kind] = kinds.get(c.kind, 0) + 1
    n_events = sum(
        len(a.score_updates) + len(a.messages) + len(a.disputes) for a in b.accounts
    )
    print(
        f"wrote {OUT}: {len(b.accounts)} accounts, {len(b.cases)} cases {kinds}, {n_events} events"
    )


if __name__ == "__main__":
    main()
