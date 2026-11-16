# Feature 08 — Deterministic market net-return comparison

Implemented on `feature/market-comparison`. No LLM, LangGraph, scraping, price forecast or fabricated live data is used. The existing authenticated batch, normalized price catalog, RouteProvider/LogisticsService and versioned crop-risk rules are reused.

## Endpoints

All responses are private/no-store and use the existing verified Supabase farmer identity.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/v1/comparisons` | Evaluate owned produce against candidate markets; return ranked eligible options and explicit exclusions |
| GET | `/api/v1/comparisons/{id}` | Retrieve an owned historical snapshot; unknown/another farmer's snapshot returns 404 |

Request example (replace the batch UUID and real known transport inputs):

```json
{
  "produce_batch_id": "YOUR_BATCH_UUID",
  "market_ids": ["dambulla-dec", "meegoda-dec"],
  "price_type": "wholesale",
  "storage_condition": "unknown",
  "assume_no_loss": false,
  "transport": {
    "route_mode": "automatic"
  },
  "market_overrides": []
}
```

`market_ids` may be omitted to evaluate all active markets, up to 20; larger catalogs require explicit selection. Each override accepts `market_id`, an optional complete `transport` options object, `expected_arrival_at` with timezone, and `additional_market_fee_lkr` (default zero). An override's transport replaces common request transport rather than partially merging it. It uses the existing logistics schema: capacity, tariff, one-way/round-trip pricing, explicit multiple deliveries, manual distance/duration, optional all-deliveries quote and required quote description. Profile capacity/rate apply when their corresponding transport inputs are absent. Duplicate IDs and overrides outside the selected markets are rejected with 422. Crop, quantity, harvest, grade, deadline and assessment time cannot be substituted by the caller.

Dates/times use UTC internally; UI dates and date-only source publication checks use Asia/Colombo. The assessment clock is captured once per comparison. Explicit arrival must not precede that clock. Single-delivery route duration can supply arrival; multiple deliveries require explicit final arrival. Route distance alone or a cost quote does not establish travel duration.

## Compatibility and price policy

- Crop and market must be active. All price imports are normalized to LKR/kg by the existing validated importer; crop default unit must be kg.
- Match the batch's crop ID, requested wholesale/retail/farmgate type and normalized exact grade. Whitespace/case normalization uses the price catalog's `key_text`; unknown grade matches only unknown grade. No convenient fallback to another grade/type/crop is allowed.
- A report must have been observed, published and imported by the assessment time. Future observations are never used as known facts. Date-only publication evidence has no invented publication time.
- Keep each source's latest known matching report, including explicit missing reports. Missing data does not resurrect that source's older price and is not treated as zero.
- Choose the newest fresh, non-missing source by observation time, import time, source key and observation ID. This chooses a reproducible observation, not the highest quoted price or an average. A newer missing report from one source may coexist with valid evidence from another source.
- Reuse `GOVINENA_PRICE_STALE_AFTER_HOURS` (default 72, validated range 1–720). Reject observations older than this limit at assessment **or expected arrival**. Exactly the boundary is accepted. Future price remains unknown even when the observed-price scenario satisfies freshness policy.

## Constraints and formulas

The deterministic `MarketComparisonService` resolves routes/transport without saving separate logistics estimate records. Existing capacity checks, explicit multiple trips, asymmetric return distances, quote scope, provider failure/manual fallback and per-delivery component rounding remain intact.

Crop risk is evaluated at expected final arrival using the current enabled crop profile version and selected storage. At/after the farmer's hard deadline is rejected. Sold/archived or already-deadline-elapsed batches are excluded before financial ranking. Guidance never extends the farmer deadline. Unknown selected loss assumptions exclude a market unless `assume_no_loss=true` is explicitly supplied. That option uses initial batch quantity, visibly labels `quantity_basis=explicit_no_loss`, and never bypasses the deadline. The returned `risk` preserves the underlying operator-rule assessment for transparency; `quantity_basis` identifies the quantity actually used for revenue. Zero saleable quantity is excluded.

For each eligible option:

```text
saleable kg = crop-risk quantity under selected storage rule
             OR original batch kg under an explicit no-loss scenario
expected gross = ROUND_HALF_UP(saleable kg × observed LKR/kg, 2 decimals)
transport = effective logistics cost (whole-batch quote OR calculated total)
additional market cost = entered whole-batch fees not already in transport
total cost = rounded transport + rounded additional market cost
expected net = rounded gross − total cost
```

All financial arithmetic uses Decimal. Existing transport component rounding is preserved; quotes replace rather than add to calculated transport totals. Tariff market handling is already inside transport; additional market fees are explicitly separate to prevent double counting. Unentered flat charges assume zero and are disclosed. Negative net proceeds are valid, displayed and ranked; no losses are clamped to zero. Order is descending net with market ID breaking ties. Repeated equal facts/inputs/time produce equal economics; route cache-hit metadata and newly generated snapshot IDs may differ.

Exclusion codes cover unknown/inactive market, unavailable/incompatible crop, closed/elapsed batch, no compatible/missing/stale/future price, capacity validation, unknown transport cost, unknown/invalid arrival, deadline violation, stale price at arrival, missing loss assumption and no remaining saleable quantity. The response carries the first applicable blocker and any evidence/transport/risk available at that stage. A catalog with no active candidates, or a comparison with no eligible options, produces valid results, not invented zero-profit markets.

## Snapshot migration and retention

Migration `a08_comparisons` follows `a07_crop_risk`. It creates `comparison_snapshots` with owner and batch foreign keys (RESTRICT), creation timestamp, JSON inputs/result, owner/batch index and PostgreSQL RLS without browser policies. Inputs capture the request, authoritative batch facts and risk-rule payload; results preserve selected observation IDs, source evidence, assumptions and formulas. Retrieval is owner-scoped and stays historical after profile/price/batch changes or archiving.

If any evaluated route uses Mapbox output, the entire comparison is not saved (`snapshot_id=null`) because existing routing terms handling forbids durable storage of provider output and derived values. The response explains this limitation. Manual/fallback/user-input comparisons can be retained. Failed comparisons do not leave separately persisted logistics estimates.

## Files

Added `app/services/market_comparison.py`, independent formula module `app/services/net_return.py`, `app/schemas/comparisons.py`, `app/models/comparison.py`, `app/api/v1/endpoints/comparisons.py`, migration and `tests/test_comparisons.py`.

Updated model/API registration, migration table expectations and reused logistics infrastructure: `TransportOptions` extracts the existing common fields without changing `/logistics/estimate` inputs; `LogisticsService.estimate(..., persist=False)` supports side-effect-free composition while preserving its default persistence behavior.

## Setup and manual verification

1. In backend `.env`, replace the existing admin UUID placeholder with `[]` or verified Supabase user UUIDs in `GOVINENA_ADMIN_USER_IDS`. Retain existing database/auth configuration; no new credentials are required. Run `.venv/bin/alembic upgrade head`, then the existing catalog seed if needed.
2. Import genuine recent verified price CSV observations through the existing admin workflow. Match the intended crop, grade and price type at multiple markets. Old sample observations must remain old; do not relabel them as live prices.
3. Configure reviewed perishability/storage assumptions using the versioned crop-risk API, or explicitly choose a no-loss scenario in the farmer comparison UI.
4. Sign in, create an owned batch with a sensible harvest/deadline, open its detail page and Compare Markets. Supply real transport inputs for at least two markets. Verify source provenance and timestamps alongside assumption labels.
5. Check gross, every transport component, additional fees and net. Change a rate or whole-batch quote and verify the revised order. Test zero observed price and negative net proceeds without treating them as missing.
6. Check arrival at/after deadline, stale/missing/mismatched prices, missing capacity, missing duration/arrival, missing loss assumptions and route failure. Review explicit exclusions. Route duration and no-loss choice do not bypass deadline checks.
7. Reload a saved manual comparison URL and verify historical values persist. A second farmer must receive 404 for its ID. Mapbox comparisons must explain non-retention.

## Actual checks and limitations

- `.venv/bin/ruff check app tests migrations` and `.venv/bin/ruff format --check app tests migrations`: passed.
- Full pytest suite via the existing sandbox selector-wait runner: **351 passed**. Focused `tests/test_comparisons.py`: **37 passed**. Coverage includes financial formulas, cents/negative nets, ranking/ties, grades/types, latest-versus-highest evidence, missing/zero/stale/future data, freshness boundary/arrival, different rates/quotes, spoilage, exclusions, authentication/ownership, validation, empty catalog, database failure and provider failure/retention.
- SQLite `alembic upgrade head`, `alembic check`, downgrade to `a07_crop_risk`, and re-upgrade: passed. PostgreSQL offline upgrade/downgrade SQL generated successfully, including RLS.
- Configured live database migration attempt failed with `OperationalError`; connection details were not printed. Live PostgreSQL migration is not claimed.
- Frontend lint/type checking/all nine Node test files passed. Default Turbopack build and Playwright server startup hit sandbox port restrictions. A complete webpack production build passed using the temporary compiler-API workaround; original frontend configuration was restored.

Commands used in the backend:

```sh
.venv/bin/ruff check app tests migrations
.venv/bin/ruff format --check app tests migrations
GOVINENA_ADMIN_USER_IDS='[]' .venv/bin/python /tmp/govinena-pytest-runner.py -q
GOVINENA_ADMIN_USER_IDS='[]' .venv/bin/python /tmp/govinena-pytest-runner.py tests/test_comparisons.py -q
GOVINENA_ADMIN_USER_IDS='[]' DATABASE_URL=sqlite:////tmp/govinena-comparison-migration.db .venv/bin/alembic upgrade head
GOVINENA_ADMIN_USER_IDS='[]' DATABASE_URL=sqlite:////tmp/govinena-comparison-migration.db .venv/bin/alembic check
```

The temporary test runner caps selector waits at 10 ms to permit TestClient cross-thread wakeups under this sandbox; it does not change application code or tests. Run ordinary `.venv/bin/pytest -q` on an unrestricted local machine. No authenticated browser test is claimed as passed: local server binding is blocked, and disposable test credentials/fresh verified test-market observations must be configured before the main Playwright flow can run.
