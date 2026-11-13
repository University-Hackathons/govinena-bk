# GoviNena backend

Python 3.12 FastAPI foundation for the GoviNena farmer market decision support system.
The current APIs demonstrate synthetic data and deterministic reasoning, not live market advice.

## Setup and run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
cd govinena-bk
uv python install 3.12
uv sync --locked
cp .env.example .env  # First setup only; preserve your existing .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
```

No virtual environment activation is needed. `uv sync --locked` creates `.venv` and installs
runtime and development dependencies from `uv.lock`. Commit the lockfile. For runtime-only
installation, use `uv sync --locked --no-dev`. Use `uv add PACKAGE` or `uv add --dev PACKAGE`
to change dependencies. Stop the server with Ctrl+C.

## Dependencies

| Library | Purpose | Group |
| --- | --- | --- |
| FastAPI, Uvicorn | REST API and ASGI server | Runtime |
| Pydantic v2, Pydantic Settings | Typed schemas and validated environment settings | Runtime |
| SQLAlchemy 2 | Synchronous database engine, ORM base, and sessions | Runtime |
| Alembic | Schema migrations, including production migration commands | Runtime |
| psycopg with binary support | PostgreSQL driver without local compilation | Runtime |
| LangGraph | Typed state graph, node execution, conditional re-evaluation | Runtime |
| LangChain, langchain-openai | Future reasoning integrations and lazy OpenAI provider | Runtime |
| HTTPX | Async external API client foundation with timeouts and connection limits | Runtime |
| pytest, pytest-asyncio | API, migration, database, and async graph tests | Development |
| Ruff | Linting and formatting | Development |
| httpx2 | TestClient transport required by the installed current Starlette | Development |

Compatible releases are constrained in `pyproject.toml` and exactly locked in `uv.lock`.
Money arithmetic uses Python's standard-library `Decimal`, without a reasoning model.

## Environment

Settings load `.env` from the backend directory. Environment variables override the file.
Restart the server after changing configuration. `.env`, database files, virtual environments,
credentials, logs, and caches are ignored by Git.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Backend-local SQLite file | PostgreSQL or SQLite SQLAlchemy connection URL |
| `OPENAI_API_KEY` | Empty | Server-side secret, required only when constructing a live provider |
| `OPENAI_MODEL` | Empty | Explicit model name for a live provider; no model is hardcoded |
| `GOVINENA_REASONING_MODE` | `mock` | Provider factory mode: `mock` or `openai` |
| `GOVINENA_APP_NAME` | `GoviNena API` | API title |
| `GOVINENA_ENVIRONMENT` | `development` | `development`, `test`, or `production` |
| `GOVINENA_LOG_LEVEL` | `INFO` | Standard uppercase log level |
| `GOVINENA_CORS_ORIGINS` | `["http://localhost:3000"]` | JSON array of allowed frontend origins |

Local fallback in `.env.example`:

```dotenv
DATABASE_URL=sqlite:///./govinena.db
GOVINENA_REASONING_MODE=mock
OPENAI_API_KEY=
OPENAI_MODEL=
```

Without `DATABASE_URL`, SQLite defaults to an absolute path inside the backend. Explicit relative
SQLite paths resolve from the command's working directory, so run the commands from this directory.

For PostgreSQL set `DATABASE_URL` to a URL in the format
`postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE`. Percent-encode special characters in URL
credentials. `postgresql://` and `postgres://` URLs are normalized to the psycopg driver.
Production mode requires PostgreSQL; it does not silently fall back after a connection failure.
Provision the database separately, then run migrations against it. Startup builds a lazy engine
without connecting, and `/health` reports process liveness rather than database readiness.

The provider factory `create_reasoner(settings)` constructs a `MockReasoner` by default. In `openai`
mode it requires a nonblank key and model, then lazily constructs `ChatOpenAI` with bounded timeout
and retries. Missing settings raise `ProviderConfigurationError` before a network call. Keys and
connection URLs use secret fields and are never returned by APIs. The demo endpoint always uses
mock reasoning, even when provider settings select OpenAI. Live LLM integration into the workflow
is future work; no paid calls are made by the current APIs.

## APIs and Swagger

Open <http://localhost:8000/docs>. Expand an operation, select **Try it out**, then **Execute**.

| Method | Route | Behavior |
| --- | --- | --- |
| GET | `/health` | Process liveness: `{"status":"ok"}` |
| GET | `/api/v1/health` | Versioned process liveness |
| GET | `/api/v1/markets` | Two clearly labeled synthetic market-price fixtures |
| POST | `/api/v1/agent/demo` | Deterministic LangGraph demonstration; JSON body required |

Example demo request (an empty object also uses these defaults):

```json
{
  "crop": "tomato",
  "quantity_kg": "100",
  "farmer_location": "Synthetic Farm",
  "production_cost": "0.00",
  "re_evaluate_once": false
}
```

```bash
curl http://localhost:8000/api/v1/markets
curl -X POST http://localhost:8000/api/v1/agent/demo \
  -H 'Content-Type: application/json' \
  -d '{"quantity_kg":"100","re_evaluate_once":true}'
```

Responses include `data_source: "synthetic"`, a notice, input fields, prices, transport estimates,
tool results, evaluated profits, a recommendation, and decision history. Decimal values serialize
as JSON strings to preserve precision. The default demonstration selects `demo-b`, with synthetic
estimated profit `12400.00` LKR: 100 kg × 135, minus 1100 transport and zero supplied production cost.
This is a test fixture result, not a real price or expected income.

Only tomato is supported by the current fixtures. Quantity must be positive and bounded; costs
must be nonnegative. Optional `selling_deadline` accepts an ISO 8601 datetime with a timezone.
Location, deadline, and free-text farmer input are retained for future reasoning but do not affect
the fixed routes or ranking yet. Unsupported crops, unknown request fields, and invalid values
return the existing structured 422 error envelope.

## Workflow and extension points

```text
PERCEIVE -> PLAN / DECIDE -> SELECT TOOLS -> EXECUTE TOOLS
  -> EVALUATE RESULTS -> RECOMMEND -> MONITOR / RE-EVALUATE
                                        | optional once
                                        +--> PLAN / DECIDE
```

`AgentState` stores typed input, crop, quantity, location, deadline, prices, transport estimates,
tool results, recommendation, and decision history. History and tool results use append reducers.
Re-evaluation is optional, deterministic, and bounded to one extra pass with a graph recursion limit.
The graph has no persistence/checkpointer or background scheduling yet. Notification delivery is a
no-op and starts no external communication.

Tool protocols define `MarketPriceTool`, `RouteEstimationTool`, and `TransportCostTool` contracts.
Their demo adapters use fixed fixtures, with no external requests. The synthetic trip formula is
300 LKR plus 20 LKR/km, using fixed 10 km and 40 km distances; vehicle capacity is not modeled.
`ProfitabilityCalculator` computes rounded revenue minus transport and supplied production cost
with `Decimal` and `ROUND_HALF_UP` at two decimal places. `RecommendationService` ranks these
results, breaking ties by market ID. Negative results remain losses. Spoilage, fees, deadlines,
market availability, and vehicle capacity are outside this foundation.

Future HTTP adapters can inject `get_http_client`; it currently makes no calls. Future persistence
services can inject `SessionDependency` and use `SQLAlchemyRepository` or domain repositories.
Synchronous sessions must be used in sync endpoints/services (or offloaded to a thread), never
shared across concurrent tasks. Endpoints contain no database queries.

## Project structure

```text
app/
  main.py                  App factory, middleware, engine/session setup, engine cleanup
  api/v1/endpoints/        Health, synthetic markets, and agent demo REST endpoints
  core/                    Settings, error responses, JSON logging
  db/                      Declarative base, engine factory, session dependency
  models/                  Future ORM model imports for Alembic discovery; no business tables
  schemas/                 Typed API inputs and outputs
  repositories/            Generic SQLAlchemy foundation and synthetic market repository
  services/                Market service, profitability, recommendation, notification, HTTP client
  agents/                  Typed LangGraph state, demo graph, mock and OpenAI provider factory
  tools/                   Tool protocols and synthetic adapters
  utils/                   Reserved for shared helpers
migrations/                Alembic environment, template, and empty initial baseline
tests/                    API, database, migration, graph, provider, and logging tests
```

## Database and migrations

A session is created per dependency invocation and always closed. Exceptions roll back pending
work; services explicitly own successful `commit()` calls. Engine SQL echo is disabled and
parameter hiding is enabled. SQLite in-memory tests use a shared static pool. No application code
calls `create_all()` and no market/business tables are created yet.

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic check
# After adding ORM models and importing them in app/models/__init__.py:
uv run alembic revision --autogenerate -m "Describe the schema change"
# Review the generated revision, then apply it:
uv run alembic upgrade head
# Only when intentionally reverting a migration:
uv run alembic downgrade -1
```

The initial baseline has no business schema changes; only Alembic's version-tracking table is
created. Both online and offline migrations load `DATABASE_URL` through the same settings and
metadata. Future migration generation sees models imported by `app.models`. Review generated
migrations before applying them to any environment.

## Errors, logging, and checks

HTTP, validation, and unexpected application errors retain the shared JSON error envelope.
Validation responses omit rejected input values and validator context. Unexpected error responses
omit exception details. Request JSON logs include generated request IDs, route templates, status,
and duration. They omit raw URLs, query parameters, request bodies/headers, configuration dumps,
and exception messages/tracebacks. Use fixed log messages and safe metadata in future code.
Uvicorn access logs are disabled; HTTP client transport logs are limited to warnings.

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Tests use isolated SQLite databases, mock reasoning, and no paid LLM requests. PostgreSQL driver
construction is tested without requiring a running PostgreSQL server. Live PostgreSQL operations
and live provider invocation require separately supplied infrastructure and credentials.

Implementation references: [SQLAlchemy sessions](https://docs.sqlalchemy.org/en/20/orm/session_basics.html),
[Alembic autogeneration](https://alembic.sqlalchemy.org/en/latest/autogenerate.html),
[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[ChatOpenAI integration](https://docs.langchain.com/oss/python/integrations/chat/openai),
[OpenAI server-side authentication](https://developers.openai.com/api/reference/overview), and
[uv projects](https://docs.astral.sh/uv/guides/projects/).
