# Feature 03 — Crop catalog and wholesale market directory

Implemented on `feature/catalog`; no merge or deployment is performed.

## Setup

With the backend environment and `DATABASE_URL` configured, run from this repository:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m app.db.seed_catalog
.venv/bin/python -m app.db.seed_catalog
.venv/bin/uvicorn app.main:app --reload
```

Both imports report `{'crops': 4, 'markets': 3}`. Run migrations before seeding;
startup does not modify schema or seed data. No extra packages or secrets are
required. Use the same database-owner connection as existing migrations. PostgreSQL
RLS blocks browser Data API roles; the verified FastAPI service reads the catalog.
The tables have no farmer write endpoints or browser-role write policies.

## API

All endpoints require the existing verified bearer session, including revocation checks.

| Method | Endpoint | Query parameters |
| --- | --- | --- |
| GET | `/api/v1/crops` | `q`, `category`, `active`, `page`, `page_size` |
| GET | `/api/v1/crops/{id}` | — |
| GET | `/api/v1/markets` | `q`, `district`, `market_type`, `active`, `page`, `page_size` |
| GET | `/api/v1/markets/{id}` | — |

Lists return `{ "items": [...], "total": 4, "page": 1, "page_size": 12 }`.
Defaults: active records, page 1, 12 records; maximum page size 100 and page
1,000,000. Total is counted after filtering; out-of-range pages return empty items.
Search is a case-insensitive substring match, including Sinhala and Tamil crop
names. Market search covers name, district and location. `%` and `_` are literal
characters rather than wildcard instructions. Ordering is name, then ID.

Categories: `vegetable`, `root_vegetable`. Market types:
`dedicated_economic_centre`, `wholesale_market`. District values reuse the existing
25-district profile schema. Detail endpoints include active status and provenance;
inactive records can be viewed by ID or listed with `active=false`.

Unknown well-formed IDs return 404; malformed IDs/filters return 422. Missing,
invalid or revoked credentials return 401; existing identity restrictions retain
403. Database query failures return a safe 503. Unsupported mutations return 405.
No market prices or crop-to-market trading relationships are introduced.

**Contract change:** `/api/v1/markets` now returns reference records in `items`,
replacing its old synthetic-price demo response. The separate agent demo and its
clearly labelled synthetic fixtures remain unchanged.

## Data, sources and seeding

`app/data/catalog.json` is the versioned source of truth. It contains:

- Tomato / තක්කාලි / தக்காளி
- Brinjal / වම්බටු / கத்தரி
- Cabbage / ගෝවා / கோவா
- Carrot / කැරට් / கரட்
- Dambulla Dedicated Economic Centre, Matale district (`dambulla-dec`)
- Meegoda Dedicated Economic Centre, Colombo district (`meegoda-dec`)
- Nuwara Eliya Dedicated Economic Centre, Nuwara Eliya district (`nuwara-eliya-dec`)

Names are cross-referenced to the Department of Agriculture HORDI crop pages in
English, Sinhala and Tamil. Each crop stores all three source URLs. Category
labels, application IDs and the `kg` default are application conventions.

Market details use [JICA Table A 3.18, T 3-31, PDF page 62](https://openjicareport.jica.go.jp/pdf/12119558_03.pdf)
for names, districts and wholesale function, corroborated against the
[Ministry of Trade 2025 progress report, Table 3.15, PDF pages 41–42](https://trade.gov.lk/storage/downloads/December2025/Z9fAUbyb1UdSIWD1LUWa.pdf).
Each market stores both URLs, titles, review date (`2026-11-13`) and notes describing
what the sources establish. Source review does not constitute a live operational
status check. `active` means enabled in this catalog; it does not guarantee today's
opening hours, services or trading availability. Location is town-level only.

**All initial market coordinates are null:** exact site coordinates were not
verified. No town-centre coordinates or inferred map pins are substituted.
Adding coordinates requires latitude, longitude, an HTTP(S) coordinate-source URL
and verification date together. Schemas and DB constraints enforce paired, bounded
coordinates and the presence of provenance. An operator must verify the source
before editing the seed file; schema validation cannot certify source accuracy.

The seed command validates the whole file before writes, rejects duplicate IDs,
and upserts stable IDs atomically with PostgreSQL/SQLite conflict handling. It
updates known records to match the checked-in file, including active status;
operator edits to seeded rows should be made in that file. It preserves records
whose IDs are absent from the file and never drops or recreates tables. A failed
write rolls back the import. Seeding needs no network access.

## Files changed

- `app/models/catalog.py`, `app/models/__init__.py`: crop/market models, keys,
  indexes, unit/category/type and coordinate constraints.
- `migrations/versions/a03_catalog_crop_and_market_reference_catalog.py`:
  `a03_catalog`, following `a02_farmer_profiles`; creates `crops` and `markets`
  and enables PostgreSQL RLS. Downgrade drops only these catalog tables.
- `app/data/catalog.json`, `app/db/seed_catalog.py`: validated, repeatable import.
- `app/schemas/catalog.py`, `app/repositories/catalog.py`, `app/services/catalog.py`:
  typed responses, pagination, literal substring filtering and safe storage errors.
- `app/api/v1/endpoints/crops.py`, `markets.py`, `app/api/v1/router.py`: read-only API.
- `tests/test_catalog.py`: seeds, rollback, validation, search, pagination, empty
  data, read-only behavior, authorization, provenance and storage failure tests.
- `tests/test_database.py`: full migration metadata expectation.
- `tests/test_agent.py`: updates the former market demo endpoint assertion to the
  reference-data contract; agent demo tests remain intact.
- `README.md`, `CATALOG.md`: setup and verification.

## Validation performed in this environment

| Command/check | Actual result |
| --- | --- |
| `.venv/bin/ruff check app migrations tests` | Passed |
| `.venv/bin/ruff format --check app migrations tests` | Passed after formatting |
| `.venv/bin/pytest -q` (45-second bound) | Stalled without output; timed out |
| `.venv/bin/python /tmp/govinena-pytest-sandbox.py -q` | 132 passed |
| SQLite Alembic upgrade, check, downgrade and upgrade | Passed |
| SQLite seed CLI twice | Both passed; 4 crops and 3 markets, no duplicates |
| PostgreSQL Alembic upgrade and downgrade `--sql` | Generated successfully |
| Migration/seed against configured PostgreSQL | Blocked by host connectivity/DNS (`OperationalError`) |

The temporary sandbox runner bounds selector sleeps to work around blocked asyncio
thread wakeups affecting even a minimal TestClient. It does not replace application
logic, auth, data or test assertions. It is not a project dependency. Run ordinary
pytest on your machine. Live PostgreSQL execution still needs verification; SQL
generation and SQLite testing are not substitutes for it.

## Manual verification

1. Configure the existing PostgreSQL and Supabase settings. Run upgrade and seed
   commands above. Re-run seed and confirm counts remain 4 and 3.
2. Start both apps, log in with a confirmed account and finish onboarding if needed.
3. Open `/markets`. Confirm three market listings and four multilingual crop cards.
4. Filter markets by Matale and search for Meegoda; confirm filters combine and
   mismatched filters show an empty state. Clear filters and search again.
5. Filter crops by Root vegetables; only Carrot should appear. Search for
   `தக்காளி` or `තක්කාලි` with all categories; Tomato should appear.
6. Open Dambulla market details, reload, inspect source links/review dates, and
   confirm the explicit unverified-coordinate message. Unknown IDs show not found.
7. With a valid bearer token, request lists with `page_size=1` and `page=2`, and
   check totals and non-overlapping IDs. Invalid district/page values return 422.
8. Sign out. Catalog API requests without credentials should return 401; direct
   workspace page access redirects to login. PUT/POST/DELETE are unavailable.
9. Stop the backend while the frontend remains open, then retry a catalog request;
   the directory should show a safe error with retry, rather than an empty list.
10. Run ordinary pytest, frontend checks/build and the catalog Playwright flow on a
    machine with database/network and local server access before merging.
