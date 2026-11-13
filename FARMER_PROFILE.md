# Feature 02: farmer profile and preferences

Implementation remains on `feature/farmer-profile` in both repositories. No branch was merged or pushed.

## Changes

- `app/models/farmer.py`: one-to-one `FarmerProfile` with a shared user primary/foreign key, cascading delete, transport and coordinate constraints, and timestamps.
- `app/schemas/farmer.py`: canonical input/output schemas, Sri Lankan districts, language/transport enums, contact normalization, approximate location checks, numeric limits, market IDs, and completion metadata.
- `app/repositories/farmer.py`, `app/services/farmer.py`: owner-scoped reads, create/update persistence, concurrent first-write handling, and completion calculation.
- `app/api/v1/endpoints/farmer.py`, `app/api/v1/router.py`: profile routes using the existing verified user dependency.
- `app/models/__init__.py`: model registration for Alembic.
- `migrations/versions/a02_farmer_profiles.py`: new table and PostgreSQL row-level security, following `a01_auth_users_sessions`.
- `tests/test_farmer.py`, `tests/test_auth.py`, `tests/test_database.py`: API, repository/service, isolation, outage, storage failure, and migration coverage.

## Contract

| Endpoint | Behavior |
| --- | --- |
| `GET /api/v1/farmer/profile` | Returns only the verified caller's profile. Missing profile returns 404; no blank profile is invented. |
| `PUT /api/v1/farmer/profile` | Creates or fully replaces only the caller's profile; returns the persisted profile with 200. |

Both routes require the existing active, confirmed Supabase session. Missing, invalid, expired, or revoked credentials return 401; forbidden authenticated identities retain the established 403 behavior. Provider outages return 503. Database errors use the existing safe 500 envelope. Validation errors return 422 without echoing personal details. Profile successes and missing-profile errors use `Cache-Control: no-store`.

The body never accepts `user_id`, timestamps, or completion fields. Caller-supplied query parameters cannot change ownership. The response includes `user_id`, timestamps, and completion metadata. PUT is full replacement: omit/null optional scalar values or send an empty market list to clear them.

Required fields: `full_name` (2–120 characters), `district` (one of 25 Sri Lankan districts), `town` (2–120 characters), `preferred_language` (`en`, `si`, `ta`), and `transport_mode` (`own_vehicle`, `hired_vehicle`, `other`). Names and towns reject control characters. Towns remain free text to support villages without an incomplete static directory.

Optional fields:

| Field | Rules |
| --- | --- |
| `contact_number` | Sri Lankan 10-digit local number or international +country-code number with 8–15 digits. Formatting spaces, hyphens, and parentheses are normalized. This is validation, not phone verification. |
| `latitude`, `longitude` | Supply both or neither. Bounds are −90…90 and −180…180. Rounded to 3 decimal places before storage. No precise GPS needed. |
| `vehicle_capacity_kg` | Greater than zero, at most 1,000,000 kg; up to 2 decimal places. |
| `estimated_transport_rate` | 0…1,000,000 LKR per kilometre; up to 2 decimal places. This is a farmer-entered estimate. |
| `preferred_market_ids` | Up to 20 IDs, each 1–64 ASCII letters/digits/underscores/hyphens, starting with a letter/digit. Duplicates are removed. No live catalogue exists yet, so existence is not validated and no market data is invented. |

Capacity and rate use SQL NUMERIC and Pydantic Decimal. JSON responses serialize these as decimal strings, preserving monetary precision; the frontend accepts and converts them for editing. Coordinates remain ordinary numbers.

A valid saved profile has all five core fields and starts at 50% completion. Five optional groups (contact, coordinate pair, capacity, rate, markets) each add 10%. `required_complete` stays true for saved profiles regardless of optional omissions. No row means no completed setup; GET returns 404. The UI explains this distinction.

## Migration and local verification

From the backend directory:

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic check
.venv/bin/uvicorn app.main:app --reload --no-access-log
```

The current development environment points at PostgreSQL. The configured database migration was attempted but failed due to unavailable database network/DNS access. Run `alembic upgrade head` locally before using the feature. No configured PostgreSQL schema was successfully changed in this environment.

On PostgreSQL, row-level security has no browser-role policies, matching the existing auth-table protection. Use the owning backend database role or an explicitly permitted server-only role. Supabase anon/authenticated Data API roles must not read farmer profiles directly. Browser requests go through Next.js and FastAPI.

Migration verification succeeded on an isolated SQLite database: upgrade to head, `alembic check`, downgrade to the auth revision, and re-upgrade. The migration test also checks the full upgrade/downgrade chain and model metadata. PostgreSQL SQL generation succeeded; live PostgreSQL execution remains unverified.

## Automated tests

```bash
.venv/bin/ruff check app tests migrations
.venv/bin/ruff format --check app tests migrations
.venv/bin/pytest -q
```

Ruff lint/format passed. All 96 backend tests passed (including the existing authentication and agent suites). This sandbox stalls ordinary FastAPI TestClient cross-thread wakeups; the suite was executed using `.venv/bin/python /tmp/govinena-pytest-sandbox.py -q`. That temporary runner caps selector sleeps at 10ms without altering application code, assertions, or authentication globally. See the prior authentication guide for the same environment issue. Test provider identity is overridden only in the isolated profile fixture; real dependency/provider-boundary tests still reject missing credentials and provider outages.

See the frontend's `FARMER_PROFILE.md` for browser instructions, E2E commands, and outcomes.
