# Time-correct evals

Evaluate an agent on historical decisions without letting it see the future.

When you replay past cases to evaluate an agent, the natural move is to join each case to your feature
tables and hand the agent whatever they say. But those tables describe the account **today**: the
chargebacks filed after the decision, the merchant's proof of delivery, the fraud team's finding. An
agent evaluated on that context looks far better than it ever could have been, because it is grading
itself against an answer key it was handed.

This example evaluates the same dispute-review agent twice over the same 36 historical disputes:

| Context | How it is built | What it measures |
| --- | --- | --- |
| **Point in time** | Chalk offline query with `input_times` = the moment each dispute was opened | What the agent would actually have decided |
| **Current values** | The same query as of today | A replay on today's feature table, leaking the future |

Quillback Card, its cardholders and their disputes are fictional. The features, offline queries, agent,
model calls and evaluations are real and run on Chalk.

## Files

| File | Role |
| --- | --- |
| `disputes/features.py` | Risk-score updates, disputes and support messages, each stamped with when it happened, and account features that aggregate them with `_.chalk_now` filters |
| `disputes/resolvers.py` | Resolvers over the fictional history (stand-ins for SQL resolvers over event tables) |
| `generate_history.py` | Writes the seeded, fictional history in `disputes/history_data.py` |
| `build_datasets.py` | Runs the two offline queries and writes one evaluation dataset per context |
| `evals.py` | The agent (a Chalk function calling a model through the Chalk model gateway), two scorers, and both evaluation runs |
| `record_timeline.py` | Weekly snapshots of a few accounts, for scrubbing through time |
| `export_page_data.py` | Condenses `results/` into the data behind the demo page |
| `results/` | The datasets, evaluation results and snapshots from the recorded run |

## How time-correctness works here

Every history row carries the time it happened. Every account feature filters on `_.chalk_now`:

```python
@features
class Account:
    id: str
    score_updates: DataFrame[ScoreUpdate] = has_many(
        lambda: ScoreUpdate.account_id == Account.id
    )
    disputes: DataFrame[Dispute] = has_many(lambda: Dispute.account_id == Account.id)

    # The latest risk score as of the query time, not the latest one ever written.
    risk_score: float = F.max_by(_.score_updates[_.score, _.ts <= _.chalk_now], _.ts)

    disputes_filed: Windowed[int] = windowed(
        "30d",
        "90d",
        "365d",
        expression=_.disputes[
            _.opened_at > _.chalk_window, _.opened_at <= _.chalk_now
        ].count(),
        default=0,
    )
```

`_.chalk_now` is the wall clock in an online query, and each row's `input_times` entry in an offline
query. So one feature definition serves live traffic and any point in the past:

```python
dataset = ChalkClient().offline_query(
    input={DisputeCase.id: case_ids},
    input_times=[
        case.opened_at for case in cases
    ],  # each case, as of when it was opened
    output=[
        DisputeCase.claim,
        DisputeCase.account.risk_score,
        DisputeCase.account.support_thread,
        ...,
    ],
)
```

## Run it

You need [`uv`](https://docs.astral.sh/uv/), a Chalk environment with evaluations enabled, and a model
gateway key stored as the Chalk secret `CHALK_ROUTER_API_KEY` (`chalk router api-key create`).

```bash
chalk apply --branch time-correct-evals     # deploy the features to a branch
./build_datasets.py                          # two offline queries -> two datasets
./evals.py smoke                             # one agent call, to check the model gateway
./evals.py run                               # both evaluations; results in results/
```

The agent model defaults to `anthropic/claude-sonnet-5-5`; set `DISPUTE_AGENT_MODEL` to change it.

## Scorers

- `decision-matches-outcome`: the built-in `json_match` scorer on the `decision` field, against what
  the fraud team concluded later (`ESCALATE` for first-party fraud, `APPROVE` otherwise).
- `cites-only-past-evidence`: a custom scorer that fails a row when the rationale cites a piece of
  evidence (a score update, dispute or message id) dated after the dispute was opened.

## License

Apache-2.0.

## Local authentication

The SDK reads credentials, API host, and the selected environment from your existing
`chalk login` in `~/.chalk.yml`, including project-scoped and default logins.
Standard `CHALK_*` environment overrides remain supported.
