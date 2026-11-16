# Feature 07 — Crop freshness and selling urgency

Implemented on `feature/crop-risk`. No price forecasting or AI reasoning is included.

## API and ownership

All routes require the existing verified Supabase identity. Responses are private and not cached.

| Method | Path under `/api/v1` | Access and behavior |
| --- | --- | --- |
| POST | `/crop-risk/evaluate` | Farmer's own batch only; other owners and unknown IDs return 404 |
| GET | `/crop-risk/profiles/{crop_id}` | Authenticated; newest version, including disabled versions; 404 if unconfigured |
| POST | `/crop-risk/profiles/{crop_id}` | Existing admin allowlist only; append a new version, return 201 |
| GET | `/crop-risk/profiles/{crop_id}/versions` | Admin only; descending immutable version history |

Evaluation input:

```json
{
  "produce_batch_id": "YOUR_BATCH_UUID",
  "storage_condition": "unknown",
  "expected_arrival_at": null
}
```

`assessment_at` is optional and defaults to server UTC time. Supplied assessment/arrival timestamps must include an offset. Assessment must follow or equal harvest, and arrival must follow or equal assessment. UTC range overflow is rejected. Crop, harvest, quantity and the hard sale deadline are resolved from the owned batch, preventing client substitutions. Unexpected fields are rejected with 422. The pure deterministic service receives all resolved inputs explicitly.

An assessment returns remaining seconds at assessment and optionally arrival, deadline status, deadline eligibility, alternative acceptance/rejection reason, urgency, optional quantity/loss estimates, selected storage, exact input timestamps, rule version, formula version and explanations. A valid request containing a late arrival returns 200 with `alternative_accepted=false`; it is a rejected selling alternative rather than a malformed request. Sold/archived batches are also rejected. It never changes batch status or deadline.

## Rules and interpretation

**No invented crop shelf lives or loss rates are seeded.** Without enabled rules, assessments still report the farmer deadline and urgency; estimated saleable quantity remains null. Configure operator assumptions after reviewing their suitability.

Admin rules accept:

- `enabled` (default true), `expected_selling_window_hours` (integer 1–8760).
- `high_remaining_percent` and `medium_remaining_percent` (integers 1–99, high < medium; defaults 25/50).
- `loss_percent_per_day`: optional map of `unknown`, `ambient`, `shaded`, `cooled` to 0–100 percent, up to two decimal places. Each condition must be configured explicitly; no rate is borrowed from another condition.
- Required `rationale` (10–2000 characters), optional `source_reference` (up to 1000 characters).

For isolated testing only, this illustrative payload exercises the calculation. It is **not validated tomato guidance and must not be treated as production reference data**:

```json
{
  "enabled": true,
  "expected_selling_window_hours": 48,
  "high_remaining_percent": 25,
  "medium_remaining_percent": 50,
  "loss_percent_per_day": {"ambient": "10", "cooled": "2"},
  "rationale": "Illustrative test scenario only; not scientifically validated.",
  "source_reference": "Local acceptance test fixture"
}
```

Post a revised complete payload to create the next version. To disable guidance, post a complete revision with `enabled=false`. The newest version controls evaluations; older versions are never overwritten or silently reactivated. PostgreSQL locks the crop row while allocating versions; the compound key also prevents duplicate versions. The API provides no update/delete operations on existing versions. Direct database operator access remains outside that API guarantee.

Algorithm `crop_risk_v1`:

1. Selling time is `max(0, farmer_deadline - assessment_time)`; arrival time is assessed separately.
2. Arrival **at or after** the farmer deadline is rejected. Guidance never extends it. Rejection withholds saleable quantity rather than claiming that all produce has physically spoiled.
3. Calculate the percentage of the harvest-to-deadline window remaining at arrival (or assessment when arrival is absent). With enabled guidance, also calculate the percentage of its harvest-to-guidance window remaining. Use the smaller percentage. High urgency is <= the high threshold, medium <= the medium threshold, otherwise low. Any rejected alternative is high. Deadline-only assessments use the disclosed 25/50 policy heuristic.
4. If a rate exists for the selected storage, linear assumed loss is `min(100, rate * elapsed_hours_since_harvest / 24)`. Quantity is `initial_kg * (1 - loss/100)`, rounded to 0.001 kg for interchange; the UI deliberately rounds more coarsely. Zero loss is supported and differs from missing loss assumptions. Constant selected conditions throughout storage are assumed.
5. Every assessment labels these as unvalidated operator assumptions, not scientific predictions or food-safety advice. Inspect the produce. No future prices are calculated.

## Database and files

Migration `a07_crop_risk` follows `a06_logistics`. It creates `crop_risk_profiles`, keyed by `(crop_id, version)`, with JSON rule payload, enabled flag, creating user, timestamp, foreign keys, positive-version constraint, and PostgreSQL RLS without browser write policies. The backend owner connection accesses the table. Downgrade removes only this feature's table and its version history.

Added `app/models/crop_risk.py`, `app/schemas/crop_risk.py`, `app/services/crop_risk.py`, `app/api/v1/endpoints/crop_risk.py`, migration and `tests/test_crop_risk.py`. Updated model registration, API router and expected migration tables in `tests/test_database.py`.

## Setup and manual verification

1. In backend `.env`, set `GOVINENA_ADMIN_USER_IDS` to `[]` or a JSON array of real verified Supabase user UUIDs. The existing `YOUR_VERIFIED_USER_UUID` placeholder prevents startup; do not use email or user-editable metadata for authorization. Keep database/auth secrets in the existing environment configuration.
2. Run `.venv/bin/alembic upgrade head`, then the existing catalog seed if needed: `.venv/bin/python -m app.db.seed_catalog`. Start the backend and frontend as described in their READMEs.
3. Sign in, create a crop batch harvested an hour ago with a deadline tomorrow, and open its detail page. With no profile, check remaining time, deadline-only urgency and unknown quantity.
4. In backend `/docs`, authorize with an allowlisted admin's existing access token. POST an explicitly reviewed crop rule to `/api/v1/crop-risk/profiles/tomato`. For local acceptance testing, the illustrative fixture above is sufficient. A normal farmer must get 403 for this route and version history.
5. Reload the batch. Select ambient storage and update the assessment. Verify the version label, coarse quantity estimate and explanation. Select an unconfigured condition and verify quantity becomes unknown rather than zero.
6. Set arrival before the deadline; update. Then set arrival at or after the deadline and verify high urgency, rejection and withheld quantity. Your saved deadline must remain unchanged.
7. Create a near-deadline batch, then examine an elapsed deadline. Test a sold/archived batch; it must be rejected. Open another farmer's ID through the API; it must return 404.
8. Post a revised profile and verify its version increments while history retains the original. Disable with a new version and verify deadline-only behavior resumes.
9. On a 390px viewport, inspect controls and explanations. Reload: scenario storage/arrival reset because they are deliberately not saved to the batch. Block the risk endpoint in browser devtools and verify the retry/error state, then restore it.

## Validation recorded in this environment

Ruff lint and formatting passed. The full backend suite passed: **314 tests**; the focused crop-risk suite passed: **36 tests**. SQLite upgrade/check/downgrade/re-upgrade and PostgreSQL offline upgrade/downgrade SQL generation including RLS passed. The configured live database could not be reached (`OperationalError`), so no live PostgreSQL migration is claimed.

Ordinary pytest stalls at TestClient in this sandbox (35-second targeted attempt timed out after the pure tests). The successful suite uses an external temporary runner that caps `selectors.DefaultSelector.select` waits at 10 ms, allowing cross-thread asyncio wakeups; application code and tests are unchanged by that runner. Commands used:

```sh
.venv/bin/ruff check app tests migrations
.venv/bin/ruff format --check app tests migrations
GOVINENA_ADMIN_USER_IDS='[]' .venv/bin/python /tmp/govinena-pytest-runner.py -q
GOVINENA_ADMIN_USER_IDS='[]' .venv/bin/python /tmp/govinena-pytest-runner.py tests/test_crop_risk.py -q
GOVINENA_ADMIN_USER_IDS='[]' DATABASE_URL=sqlite:////tmp/govinena-risk-migration.db .venv/bin/alembic upgrade head
GOVINENA_ADMIN_USER_IDS='[]' DATABASE_URL=sqlite:////tmp/govinena-risk-migration.db .venv/bin/alembic check
```

The selector workaround is test-runtime-only and is not committed into production code. Run ordinary `.venv/bin/pytest -q` on an unrestricted local machine.
