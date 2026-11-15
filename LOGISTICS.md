# Feature 06 — Route and transportation cost estimation

Implemented on `feature/logistics`. No merge or deployment is performed. The
existing landing page, authenticated workspace, produce/catalog/price features and
separate synthetic agent demo are preserved. No AI reasoning is connected.

## Setup and current configuration blocker

The current backend `.env` contains the literal admin placeholder
`YOUR_VERIFIED_USER_UUID`. It is not a UUID, so Settings validation prevents normal
startup and Alembic execution. Replace it with a real verified provider UUID, or
use the following to disable price-admin imports while running logistics:

```dotenv
GOVINENA_ADMIN_USER_IDS=[]
```

The file was not automatically changed. Checks here used a process-local empty
allowlist override; this does not persist a settings change or grant admin rights.

From the backend after correcting configuration:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m app.db.seed_catalog
.venv/bin/uvicorn app.main:app --reload
```

No additional dependencies are required. The new migration `a06_logistics` follows
`a05_market_prices`; it creates `logistics_estimates` with owner/batch/market foreign
keys, an owner/batch/time index and PostgreSQL RLS without browser-role policies.
The backend uses the existing database-owner connection. Foreign keys restrict
physical deletion of referenced data. Produce soft-archiving retains history.
Downgrade intentionally drops the estimates table and its history.

### Optional Mapbox routing

Set these in **`govinena-bk/.env`**, then restart the backend:

```dotenv
MAP_PROVIDER=mapbox
MAPBOX_ACCESS_TOKEN=YOUR_SERVER_DIRECTIONS_TOKEN
GOVINENA_ROUTING_ALLOW_MAPBOX_CACHE=false
GOVINENA_ROUTE_CACHE_TTL_SECONDS=300
```

A frontend `.env.local` token alone does not configure FastAPI. Use a token allowed
for server-side Directions requests; browser URL restrictions may reject server
calls. The token remains server-side and is never stored with assumptions or exposed
in exception text. Without a token/provider, manual distance and known quotes work.
`MAP_PROVIDER=manual` is the default and makes no external route calls.

Automatic routing requires an optional batch source pin and **verified market
coordinates**, including source/date metadata. The initial catalog intentionally
has no verified market coordinates. Its markets therefore use the manual-distance
or quote fallback. Do not substitute town-centre/geocoded guesses. Operators can
maintain verified coordinates through the existing catalog seed workflow when a
reliable source is available.

Mapbox Directions v5 receives longitude/latitude waypoints with the `mapbox/driving`
profile. Distances are converted from meters to km and durations from seconds to
minutes. Return-trip pricing requests origin→market→origin and uses the distinct
return leg. The adapter does not invent travel speed, straight-line road distance,
traffic conditions, truck restrictions, toll amounts or waiting time. Route failures,
timeouts and malformed responses become safe fallback reasons.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/v1/logistics/estimate` | Estimate owned batch transportation and record completed assumptions |
| GET | `/api/v1/logistics/estimates?batch_id=UUID` | Latest 20 owner-scoped input/result snapshots |

Both reuse existing verified authentication and session revocation checks. The
produce quantity and origin are read from the owned batch; callers cannot assign
an owner or supply a different quantity. Unknown/other-owner batch IDs return 404.
Unknown markets return 404; inactive markets return 422; archived batches cannot
receive new calculations (409) but their history remains readable. Responses are
private/no-store. Database errors become safe 503 responses.

### Request

- `produce_batch_id`, `market_id`: required references.
- `route_mode`: `automatic` (default) or `manual`.
- `pricing_basis`: `one_way` (default) or `round_trip`; reflects the vehicle's charge
  agreement rather than a claim that the vehicle never returns.
- `transport_mode`: own/hired/other; optional fallback to the farmer profile.
- `vehicle_capacity_kg`: positive, up to 1,000,000 kg, at most 3 decimals; defaults
  to profile capacity when omitted/null. Capacity is required even with a quote.
- `allow_multiple_trips`: false by default. Over-capacity loads are rejected unless
  explicitly enabled. More than 100 deliveries is unsupported and rejected.
- `tariff`: `base_charge_lkr`, `rate_per_km_lkr`, `loading_lkr`, `unloading_lkr`,
  `toll_lkr`, `market_handling_lkr`. Components are nonnegative LKR with up to 2
  decimals. Flat charges default to an explicit zero assumption; a missing km rate
  uses the profile estimate if present. No production fare is seeded or invented.
- `manual_distance_km`, optional `manual_duration_minutes`: user-supplied outbound
  road distance/time. Unknown duration stays null; an explicit zero is preserved.
- `manual_return_distance_km`, optional `manual_return_duration_minutes`: independent
  return inputs for round-trip pricing. Alternatively, explicitly set
  `assume_equal_return=true`. Equal distance never implies equal travel duration.
- `known_quote_lkr`, `quote_note`: optional all-in quote for the whole batch and
  every delivery. A nonblank description of at least 3 characters is required.

Manual distances are bounded at 20,000 km, durations at 100,000 minutes; unknown
fields, nonfinite/negative values and inconsistent return fields are rejected.
A return duration may accompany an explicitly equal-distance return assumption.
One-way requests reject return fields; an explicit return distance and the equal
assumption cannot both be supplied. Monetary inputs are bounded at 1 billion LKR;
calculated totals above 1 trillion LKR are rejected. These are application bounds,
not agricultural or legal vehicle limits.

### Deterministic formula

```text
trips = ceil(batch_quantity_kg / vehicle_capacity_kg)
billable_km = trips * (outbound_km + return_km if round_trip else outbound_km)
base = trips * base_vehicle_charge
distance_charge = billable_km * rate_per_km
loading = trips * loading_charge
unloading = trips * unloading_charge
tolls = trips * toll_charge
handling = trips * market_handling_charge
calculated_total = base + distance_charge + loading + unloading + tolls + handling
```

Each component total is rounded to 2 decimals with Decimal/ROUND_HALF_UP after trip
multiplication. The rounded components are summed, so the displayed breakdown adds
up. Base/flat charges are not doubled just because pricing is round-trip; users must
enter the complete flat charges for their agreement. Trip loads are returned
explicitly, e.g. 100 kg at 40 kg capacity gives 40+40+20 kg.

The known quote replaces the calculated total and is never multiplied by trips.
Where route and km rate exist, the calculated breakdown remains visible as reference.
With a known quote, a missing route or missing km rate does not fabricate a distance
or calculated total: quote cost is available, route fields remain null. Unsupported
capacity still fails validation. Without enough route/rate data or a quote, the API
returns `status=needs_input`, null cost, helpful messages, and no saved record.

Output includes route source (`mapbox`, `manual`, `unavailable`), distance, duration,
return assumptions, cache flag, per-component charges, billable distance, delivery
loads, selected total and source (`calculated`/`user_quote`/`unavailable`). Durations
are per route leg; total elapsed delivery time, loading queues and repositioning
schedules are not inferred.

## Assumptions, storage and cache policy

Completed records snapshot quantity, capacity, delivery loads, resolved rates/fees,
profile versus entered sources, transportation mode, billing basis, source/market
locations, quote scope/note, explicit return assumptions and formula/rounding version.
Later batch/profile changes do not rewrite old records. History is owner-scoped,
latest 20; it is not an unbounded audit-list API. Failed/needs-input calculations
are not saved as completed estimates.

Manual-distance calculations retain their route and result because these are user
inputs. Mapbox results and their derived calculated totals are not stored in durable
history; only the input assumptions are retained. User-entered whole-batch quotes
can be retained independently. API responses themselves use no-store headers.

The [published Mapbox product terms, section 2.5](https://assets-global.website-files.com/5d4296d7a839ea49599adba1/6609f33f5ac2ae69b3a2de50_Mapbox%20Product%20Terms%20%282024-03-31%29.pdf)
restrict caching/storing Directions responses under standard terms. Therefore
Mapbox caching is disabled by default. Only an operator with an agreement permitting
it should enable `GOVINENA_ROUTING_ALLOW_MAPBOX_CACHE`. Successful routes can then
use the bounded process-local TTL/LRU cache (256 entries, default 300 seconds,
configurable 0–3,600 seconds). Keys include direction/billing basis and coordinates;
return routes are not substituted with one-way results. Provider failures are not
cached. User-entered manual route values can be cached by matching inputs. No route
geometry, token or provider HTTP response is stored in the database.

Adapter contract: [Mapbox Directions API](https://docs.mapbox.com/api/navigation/directions/).
Live Mapbox calls were not verified in this environment; mocked tests cover coordinate
order, unit conversion, return legs, timeouts, malformed responses and safe fallback.

## Files changed

- `app/providers/routes.py`: RouteProvider protocol, Mapbox adapter, manual route
  construction and bounded TTL cache.
- `app/services/transport_cost.py`: deterministic Decimal cost/capacity calculator.
- `app/schemas/logistics.py`: validated inputs, route/cost responses and history.
- `app/models/logistics.py`, `app/repositories/logistics.py`, `app/services/logistics.py`:
  ownership, provider/fallback selection and recorded assumptions.
- `app/api/v1/endpoints/logistics.py`, `app/api/v1/router.py`: authenticated API.
- `app/core/config.py`, `app/main.py`, `.env.example`, `app/models/__init__.py`:
  provider configuration, app-scoped cache and metadata registration.
- `migrations/versions/a06_logistics_transport_calculation_assumptions.py`: new table,
  references/index and PostgreSQL RLS.
- `tests/test_logistics.py`, `tests/test_database.py`: calculations, ownership,
  cache/provider/quote/failure tests and migration expectations.
- `README.md`, `LOGISTICS.md`: setup, actual checks and manual verification.

## Actual verification

Checks used `GOVINENA_ADMIN_USER_IDS='[]'` as a temporary environment override to
isolate the invalid local admin placeholder; the local `.env` was not edited.

| Command/check | Actual outcome |
| --- | --- |
| `.venv/bin/ruff check app tests migrations` | Passed |
| `.venv/bin/ruff format --check app tests migrations` | Passed |
| Ordinary logistics pytest, bounded to 35 seconds | Stalled without output; timed out |
| `.venv/bin/python /tmp/pytest-sandbox-prices.py -q` | 278 passed (full suite) |
| SQLite upgrade/check/downgrade/upgrade | Passed |
| PostgreSQL upgrade/downgrade `--sql` | Generated successfully |
| Configured PostgreSQL migration after admin-ID override | Failed with connectivity/DNS OperationalError |
| Normal migration before override | Blocked by invalid admin UUID placeholder |

The temporary selector runner works around sandbox asyncio thread wakeups that stall
even a minimal TestClient. It does not replace application logic or assertions and
is not a runtime dependency. Run ordinary pytest locally. SQLite/offline SQL checks
do not prove live PostgreSQL execution, and provider mocks do not prove a live route.

## Manual verification

1. Correct the admin setting, apply migrations, seed the existing catalog and start
   both applications. Sign in as a farmer and complete onboarding.
2. Open an owned, unarchived produce batch. In Transportation cost choose a market,
   capacity and profile/entered vehicle preferences.
3. For a 100 kg batch choose 40 kg capacity and estimate. Verify rejection until
   multiple deliveries are explicitly enabled.
4. Choose manual distance 10 km, one-way rate 20 LKR/km, base charge 300 per delivery,
   and zero other fees. Three deliveries should total **1,500 LKR**.
5. Switch to round-trip pricing and enter return distance 15 km. The total should
   be **2,400 LKR**. Try the equal-return checkbox only after clearing explicit
   return distance; that assumption should be labeled in the result.
6. Enter a whole-batch quote of 700 LKR and a description. Selected cost should be
   **700 LKR**, not 700 per delivery. Reload and expand the saved inputs to verify
   the recorded quote, capacity, charges and quantity snapshot.
7. Try automatic routing without source/verified-market coordinates and without a
   manual distance. Confirm helpful needs-input state and nulls, not a zero estimate.
   Supply a known distance or quote and retry. Manual duration may remain unknown.
8. When verified market coordinates and a valid backend Mapbox token are available,
   check an API route and distinct return leg. Do not enable Mapbox caching unless
   the account agreement permits it. API route results should not persist in history.
9. Test negative fees, zero capacity, missing quote description and unsupported trip
   count. Test a second farmer's batch ID; POST/history should return 404.
10. Archive the batch; new calculations are unavailable and previous assumptions
    remain readable. Test anonymous requests (401) and storage/provider outages.
11. Re-run ordinary backend tests, frontend production build and Playwright locally
    before merging. No AI recommendation should become executable.
