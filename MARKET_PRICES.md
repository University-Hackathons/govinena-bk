# Feature 05 — Agricultural market price management

Implemented on `feature/market-prices`. Existing authentication, profiles, catalog,
produce, landing page and workspace layout remain in place. No AI recommendation
logic, live-price feed or scraper is introduced.

## Setup

From the backend with its existing virtual environment and database configured:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m app.db.seed_catalog
.venv/bin/uvicorn app.main:app --reload
```

No additional packages are required. Migration `a05_market_prices`, following
`a04_produce_batches`, creates `market_price_observations`, `market_price_imports`
and `market_price_import_rows`. It includes foreign keys, lookup indexes, unique
file/observation keys, nonnegative-price checks and PostgreSQL row-level security.
The backend database-owner connection performs authorized access; browser Data API
roles have no policies granting direct access. Downgrade drops these three tables
and their audit data and must only be used intentionally.

Add the following to **`govinena-bk/.env`**, replacing the UUID placeholder with a
real verified Supabase user ID, then restart the backend:

```dotenv
GOVINENA_ADMIN_USER_IDS=["YOUR_VERIFIED_USER_UUID"]
GOVINENA_PRICE_STALE_AFTER_HOURS=72
```

The UUID is available from the existing authenticated `GET /api/v1/users/me`
(or the frontend `/api/users/me` bridge). Do not use an email address or session ID.
An empty allowlist `[]` denies all imports. Admin authorization is server-controlled
and checked after the existing provider/session verification; user-editable metadata,
frontend state and email cannot grant it. The frontend needs no separate admin secret.
The existing workspace guard requires UI users, including admins, to complete onboarding.

No price observations are automatically imported. The existing catalog seed creates
reference crops/markets only. Import the sample explicitly through the admin UI if
wanted. Use a separate non-admin account when testing farmer permissions.

## Endpoints

All endpoints require existing verified authentication; import/audit/download require
an allowlisted admin.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/v1/prices/access` | Current account's import capability |
| GET | `/api/v1/prices/latest` | Paginated latest observation per series |
| GET | `/api/v1/prices/history` | Bounded historical observations |
| GET | `/api/v1/prices/comparison` | Crop observations across catalog markets |
| GET | `/api/v1/prices/freshness` | Age and missing-coverage summary |
| POST | `/api/v1/prices/imports` | Verified raw CSV upload |
| GET | `/api/v1/prices/imports/{id}` | Paginated original fields and row outcomes |
| GET | `/api/v1/prices/imports/{id}/file` | Original upload, byte-for-byte attachment |

Read filters: optional `crop_id`, `market_id`, `quality`, `source_name`, and
`price_type` (wholesale by default; retail/farmgate are separate). Source/quality
filters are normalized exact matches; an explicit `quality=` selects unspecified
grade. Omission selects all qualities without averaging them. History requires both
crop and market; comparison requires crop. Latest/comparison have `page` and
`page_size` (default 20, maximum 100). Latest partitions by crop, market, type,
normalized source and normalized grade; never by import order alone.

History accepts inclusive Sri Lankan `start_date`/`end_date`; defaults to 30 days
ending today. Reversed/future/over-366-day ranges are rejected. `limit` defaults to
1,000, maximum 5,000; `total` and `truncated` explicitly describe clipping. History
retains inactive reference records, whereas latest/comparison/coverage use active
catalog records. No prices are averaged, ranked as recommendations or filled in.

404 means an unknown catalog/import ID; invalid filters return 422. Missing/invalid
credentials are 401 and non-admin import/audit attempts are 403. Storage failures
return safe 503 errors. Upload size/type errors return 413/415. Successful imports
return 200 with row outcomes, including imports with rejected rows, so callers can
inspect durable audit records rather than losing the rejection report.

## CSV contract and provider

The `MarketPriceProvider` protocol is implemented by `VerifiedCSVProvider`. It parses
local bytes only, never visits a source URL and performs no scraping. Verification
is an authorized operator's source check plus schema/consistency validation; the
software cannot certify the truth or licensing of an arbitrary source document.

POST a UTF-8 (optionally BOM-prefixed) CSV with `Content-Type: text/csv` and query
parameters `verified=true`, `verification_note` (10–1,000 nonblank characters), and
optional `filename`. Maximum 1 MiB / 2,000 data rows. Column order may vary, but all
these headers must occur exactly once and unknown headers are rejected:

```csv
crop_id,market_id,observation_date,observed_at,price,unit,unit_quantity,currency,price_type,quality,source_name,source_url,document_reference,source_publication_date,source_published_at
```

| Field | Contract |
| --- | --- |
| `crop_id`, `market_id` | Existing active catalog IDs |
| `observation_date` | `YYYY-MM-DD` when only the day is known |
| `observed_at` | Timezone-aware ISO timestamp when exact time is known; supply this OR observation_date |
| `price` | LKR amount for the stated unit quantity; plain decimal without grouping separators |
| `unit` | `kg`, `g`, or `tonne`; unweighed bags/crates are not guessed |
| `unit_quantity` | Positive quantity of the stated unit, e.g. 50 kg for a bag whose mass is verified |
| `currency` | `LKR` only; no guessed exchange rates |
| `price_type` | `wholesale`, `retail` or `farmgate` |
| `quality` | Optional source-described grade; blank means unspecified |
| `source_name` | Required source name |
| `source_url` / `document_reference` | At least one required; URL must be HTTP(S) without embedded credentials |
| `source_publication_date` | Required source publication date, interpreted in Sri Lanka time |
| `source_published_at` | Optional exact timezone-aware publication timestamp; leave blank when unknown |

Day-only observations use a canonical Sri Lankan midnight internally, explicitly
marked `observation_precision=day`. The UI displays the day and marks its precision;
this does not claim collection occurred at midnight. Source publication timestamps
remain null when only a date is known. Future observations/publications, publication
before observation, disagreeing date/timestamp, invalid values and negative prices
are rejected. Exact observation times have a five-minute clock-skew allowance.

Normalization divides source price by `unit_quantity × kg_per_unit` using Decimal,
then rounds to six decimal places in LKR/kg. Overflow or rounding a positive amount
to zero is rejected. Original amounts/units remain in the immutable upload/row audit.
Blank price, `NA`, `N/A`, or `n.a.` becomes null. Explicit `0` remains a real reported
zero. No missing value is replaced with zero or a previous observation.

### Idempotency and audit

- Exact upload bytes have a unique SHA-256. Re-uploading the same file returns the
  original report with `replayed=true`; filename or verification-note changes do
  not create a new import for identical bytes.
- Observations have a unique canonical key: crop, market, UTC observation instant,
  price type, normalized grade and source name. The key excludes price so conflicting
  values cannot silently overwrite one another.
- Equal normalized values for the same key are skipped as duplicates, including
  equivalent unit representations; different values are rejected as conflicts.
- Every logical CSV data row retains its original fields, row number, outcome,
  error code and linked observation ID when accepted/duplicate. File/header-level
  failures use row 0 and retain the original bytes.
- Valid rows may be accepted while other rows are rejected. All records and audit
  rows commit in one transaction; database failures roll back the whole import.
- Structured logs retain import ID, row number and safe error codes/counts. CSV
  values, verification notes and credentials are not emitted in logs.
- Retrying a timed-out request with the identical file is safe. Correct rejected
  rows in a new file to retry them; exact-byte replays do not reprocess failed rows.
- There is no mutation/deletion API for observations or audit records. Source
  revisions conflicting with an existing key require an explicit future correction
  workflow; this feature will not silently replace the original value.

## Freshness and comparison

The default stale threshold is 72 hours from observation time, configurable from
1–720 hours. Importing old data today does not make it recent. A latest missing
observation remains missing even if an older reported price exists. Zero remains
reported and is included in coverage.

Freshness reports recent/stale/unreported series and catalog crop-market pairs
without any latest reported value for the selected type/source/grade. This matrix
is catalog coverage, not a claim that every crop trades at every listed market.
Comparison preserves markets without matching records using empty observations.
Multiple grades/sources remain separate; no “best market” is inferred.

## Historical sample provenance

`app/data/verified_prices_sample.csv` contains 16 manually transcribed observations:
Tomato, Carrot, Cabbage and Brinjal; Dambulla; wholesale and retail; 29–30 December
2025. They come from the Yesterday/Today columns of the official
[CBSL Daily Price Report, 30 December 2025, table on page 2](https://www.cbsl.gov.lk/sites/default/files/cbslweb_documents/statistics/pricerpt/price_report_20251230_e.pdf).
Source values were checked against the official indexed table on 15 November 2026.
Direct PDF retrieval returned 403 in this environment; no automated scraping or
access-control bypass was used. An importing admin must review the cited source.

The rows carry that URL and per-row document/column references. Publication date
is 30 December; exact observation/publication times and grades were not available
and are not fabricated. The CSV is a historical verification fixture, not a current
feed. It must display outdated after import. It is not automatically seeded.

## Files changed

- `app/models/prices.py`, `app/models/__init__.py`: observations and immutable import audit.
- `app/schemas/prices.py`: normalized/response/provenance schemas.
- `app/providers/market_prices.py`: provider protocol and strict CSV parser/normalizer.
- `app/repositories/prices.py`, `app/services/prices.py`: import, idempotency,
  windowed latest selection, history, comparison, freshness and audit/download.
- `app/api/v1/endpoints/prices.py`, `app/api/v1/router.py`: authenticated endpoints.
- `app/core/auth.py`, `config.py`, `logging.py`, `.env.example`: default-deny admin
  IDs, freshness configuration and safe import-log context.
- `migrations/versions/a05_market_prices_audited_market_price_observations.py`:
  the three tables, indexes, constraints and PostgreSQL RLS.
- `app/data/verified_prices_sample.csv`, `tests/test_prices.py`,
  `tests/test_database.py`: verified fixture, feature tests and migration metadata.
- `README.md`, `MARKET_PRICES.md`: setup and verification.

## Actual validation

| Command/check | Result |
| --- | --- |
| `.venv/bin/ruff check app tests migrations` | Passed |
| `.venv/bin/ruff format --check app tests migrations` | Passed |
| Ordinary pytest for price tests, bounded to 60 seconds | Stalled without output; timed out |
| `.venv/bin/python /tmp/pytest-sandbox-prices.py -q` | 244 passed |
| SQLite upgrade/check/downgrade/upgrade | Passed |
| PostgreSQL upgrade and downgrade `--sql` | Generated successfully |
| Migration against configured PostgreSQL | Blocked by connectivity/DNS `OperationalError` |

The scratch sandbox runner bounds selector waits to work around blocked asyncio
thread wakeups affecting even a minimal TestClient here. It changes neither
application logic nor test assertions. It is not a runtime dependency; run ordinary
pytest locally. Live PostgreSQL execution and browser verification remain required.

## Manual verification

1. Migrate and seed the existing catalog. Configure the backend admin UUID and
   restart it. Start the frontend and complete normal onboarding for test accounts.
2. As an admin, open Markets → View market prices → Import prices. Download the
   historical sample, review its source and mappings, supply a verification note,
   check the confirmation, and import it.
3. Confirm 16 accepted rows, no rejected rows and the original-file download.
   Re-import the same CSV; verify replayed status and unchanged observation count.
4. Filter Tomato/Dambulla/Wholesale. The latest sample is 345 LKR/kg on 30 December
   2025, marked outdated. Retail is a separate 375 LKR/kg observation.
5. Set history dates 2025-12-29 through 2025-12-30. Check wholesale 375→345 and
   retail 405→375. Missing days must not be zero-filled or joined through.
6. Clear the market filter to compare the crop across all active catalog markets;
   unobserved markets show missing records, not invented prices.
7. Import a modified test CSV with a negative value, unknown crop, unsupported unit,
   conflicting same-key price and a blank price. Verify per-row audit outcomes.
   Use a disposable database/account for these artificial validation cases.
8. Sign in as a non-admin: import controls are absent, direct UI access is blocked,
   and import/audit/original-file endpoints return 403. Anonymous reads return 401.
9. Stop the backend after opening the page; retry reads/imports and check safe
   errors rather than empty-success states. Restore it and retry.
10. Run ordinary backend pytest, the frontend production build and Playwright tests
    on a machine with database/network and local port access before merging.
