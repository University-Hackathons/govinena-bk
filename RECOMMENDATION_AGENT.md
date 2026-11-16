# Feature 09 — LangGraph market recommendation agent

Branch: `feature/ai-agent`. This is the real-data workflow; `/api/v1/agent/demo` remains a separate, labelled synthetic demo and is never called by the farmer recommendation flow.

## API

Existing Supabase identity validation and farmer ownership checks apply to every operation. Normal responses are private/no-store. No endpoint executes a sale, payment, booking or notification.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/v1/agent/recommendations` | Validate an owned available batch, save a queued run, return 202, dispatch bounded execution |
| GET | `/api/v1/agent/runs/{id}` | Owner-scoped public run view; never returns checkpoint blobs or provider exceptions |
| GET | `/api/v1/agent/runs?page=1` | Owner-scoped run list, 20 per page |
| POST | `/api/v1/agent/runs/{id}/resume` | Revision-checked missing-input/failed/queued recovery, or recovery after an expired execution lease; returns 202 |
| POST | `/api/v1/agent/runs/{id}/decision` | Record `accept` or `reject`; no transaction or produce status change |

Creation accepts the existing comparison input shape with at most **five** selected candidate markets/overrides. `produce_batch_id` is authoritative; no caller-provided user ID, tool names, prices, quantities or assessment time are accepted. Common transport options and per-market complete overrides, price type, known storage and explicit no-loss scenario reuse Feature 08 validation. A missing market list selects active markets only when at most five exist; otherwise the workflow requests a bounded selection.

Resume body:

```json
{
  "revision": 0,
  "constraints": {
    "produce_batch_id": "YOUR_EXISTING_BATCH_UUID",
    "market_ids": ["dambulla-dec"],
    "price_type": "wholesale",
    "storage_condition": "unknown",
    "assume_no_loss": false,
    "market_overrides": []
  }
}
```

Supply the latest revision returned by GET. Constraints may be omitted to retry the same inputs. A resume cannot switch user or batch. Completed/accepted/rejected decisions are preserved: modify them by creating a new run, rather than overwriting the earlier decision. Repeated same decisions are idempotent; conflicting decisions/revisions return 409. Acceptance also checks current batch facts/status, active crop/selected market, current observation age, future arrival and hard deadline; changed or expired scenarios must be recalculated.

## Workflow and trust boundaries

`MarketAgentState` explicitly contains authenticated user/batch IDs, structured farmer constraints, observations, selected tools and tool index, execution outcomes, transport/risk data, comparison, recommendation/explanation, status, warnings, missing-input questions, step count and concise stage summaries. Wire payloads remain JSON; nested inputs/outputs are validated by the existing Pydantic request and service-response schemas at tool boundaries.

LangGraph nodes:

1. Perceive current owned batch context; require available status and a future hard deadline.
2. Check completeness (including vehicle capacity).
3. Plan/select tools. In OpenAI mode the LLM selects from a typed enum only. Mandatory context/comparison tools and transport dependencies are normalized into a safe execution order.
4. Execute one selected verified tool per graph step, checkpointing its actual outcome before the next tool.
5. Evaluate authoritative comparison output.
6. Request missing input with a native LangGraph interrupt when necessary; updated constraints resume the same durable thread and re-check current data.
7. Generate a recommendation anchored to deterministic rank one.
8. Persist the public result/status through the SQL-backed checkpointer.

Allowlisted typed adapters:

- `market_context`: owned batch and active catalog candidates.
- `price_observations`: existing price service with crop/type/exact-grade filters and source timestamps.
- `transport_estimates`: existing LogisticsService/RouteProvider, capacity checks, tariff/quote scope and manual fallbacks.
- `crop_risk`: existing deterministic risk service and versioned crop/storage assumptions.
- `market_comparison`: existing MarketComparisonService; revalidates all price, route, loss and deadline facts and calculates final figures without a separate comparison snapshot.

The final calculator is authoritative even when preliminary tool observations differ due to intervening data/time changes. The selected market and returned net must equal its first eligible option. Missing/stale prices, unsupported loads, missing route/arrival, unknown loss assumptions and violated deadlines never become fabricated figures. A farmer may explicitly choose a no-loss scenario; it remains an assumption and never extends a hard deadline.

The LLM receives only controlled context/outcome flags, not source documents, farmer notes, coordinates, credentials or arbitrary farmer instructions. It may select tools and compose a short explanation from typed, approved factual sentences. The sentence enum deliberately prevents invented numbers, promises and unsupported actions. Figures and market selection come only from the verified calculator. Invalid/unavailable LLM output falls back to deterministic planning/grounded wording with visible warnings. No hidden model chain-of-thought, raw generations or exception diagnostics are stored in run views/traces.

## Configuration

Use the existing backend `.env` (never frontend public variables):

```dotenv
GOVINENA_REASONING_MODE=mock
# For optional live planning/explanation, set mode to openai and configure:
# OPENAI_API_KEY=<your server-side key>
# OPENAI_MODEL=<your supported configured model>
GOVINENA_AGENT_MAX_STEPS=40
GOVINENA_AGENT_MAX_ATTEMPTS=2
GOVINENA_AGENT_TIMEOUT_SECONDS=180
```

Mock mode needs no LLM key and uses the same real services/reference observations; it never uses synthetic live prices. A run retains its original reasoning mode when resumed. OpenAI construction reuses the existing provider factory with internal retries disabled; the graph owns retry limits. No paid LLM invocation was made during implementation tests.

Validated bounds: total graph-step budget 12–60 (default 40), attempts per tool/LLM operation 1–3 (default 2), and whole-invocation timeout 30–300 seconds (default 180). Each model call also has a bounded timeout. At most five resume revisions are accepted and the persisted total step budget is not reset by resume. Client/provider output cannot choose arbitrary functions, SQL, shell commands or financial actions.

If the existing `.env` still contains the admin UUID placeholder, replace it with `GOVINENA_ADMIN_USER_IDS=[]` or real verified admin UUIDs before starting the application. This pre-existing configuration issue was not silently edited.

## Durable execution and retention

Migration `a09_agent` follows `a08_comparisons` and creates `agent_runs`: farmer/batch foreign keys with RESTRICT, status, approval, revision, timestamps, execution-token/lease, JSON state/resume input/checkpoint and owner/creation index. PostgreSQL RLS is enabled without browser policies. The public API is owner-scoped and never exposes serializer blobs.

`DurableSaver` delegates LangGraph serialization to the installed InMemorySaver and persists its checkpoint/writes/blobs as a JSON/base64 envelope in the existing SQL database at every step. It restores a new saver per execution/resume; no process-local graph cache is required for recovery. No pickle fallback or user-supplied serialized checkpoint is accepted. This adapter is tested against the locked LangGraph version; keep its persistence compatibility tests when upgrading dependencies.

An atomic database claim and execution token prevent duplicate workers for a queued run. The lease lasts ten minutes, longer than the bounded invocation timeout. Background execution uses fresh database sessions and the existing HTTP-client/routing infrastructure. Interrupted queued work or an expired running lease can be recovered through Resume. **This is an in-process background dispatcher, not a durable job broker**: after a hard restart it does not automatically sweep queued jobs. The frontend exposes recovery, and no transaction is ever replayed because tools are read-only. Do not claim automatic crash recovery without that resume action.

Existing Mapbox retention restrictions apply to checkpoint state too. A provider-derived route/cost never enters durable agent output; the workflow requests user-confirmed manual inputs before retaining a recommendation. A manual distance/duration or a whole-batch quote with explicit final arrival supports durable runs. Provider failures use existing manual fallbacks; missing facts remain input requests. No route output is sent to the LLM.

## Files

Added `app/agents/{market_state,market_reasoner,market_tools,market_graph,checkpoint}.py`, `app/models/agent_run.py`, `app/schemas/recommendations.py`, `app/services/agent_runs.py`, the `a09_agent` migration and `tests/test_recommendations.py`.

Updated the existing provider factory's optional retry/timeout parameters, agent router, settings, model registration and migration table expectations. MarketComparisonService now accepts `persist=False` for agent composition; its normal Feature 08 snapshot behavior is unchanged. No new authentication service or dependency is introduced.

## Manual verification

1. Apply `.venv/bin/alembic upgrade head`, seed the existing catalog if needed, and start backend/frontend. Set mock mode for initial verification.
2. Import genuinely verified recent prices with matching crop, type and grade at candidate markets. Configure reviewed crop/storage assumptions, or explicitly select a no-loss scenario. Do not relabel historical sample prices as current.
3. Sign in, create an available batch with a sensible harvest/deadline, open its detail page and click Generate Recommendation. Choose up to five candidates and supply valid capacity, distance/duration or quote/final-arrival inputs.
4. Watch queued/running status and the actual tool timeline. Verify selected market/net equal deterministic rank one, and inspect alternatives, sources, timestamps, cost and risk assumptions.
5. Omit capacity, transport/arrival or applicable loss assumptions, or use stale/missing prices. Confirm an input request with no invented recommendation. Modify Constraints and resume the same run; updated data must be rechecked.
6. Restart the backend after a run is paused for input; resume and verify the checkpoint survives. For a forcibly interrupted running process, wait for the lease to expire before recovery. For a completed decision, modifying constraints creates a new run and preserves the old result.
7. Accept, reload and verify accepted state persists without changing produce status or arranging a sale. Reject a different run and reload. Another farmer must get 404 for the original ID. Change quantity/deadline or let the result become stale and verify acceptance returns 409.
8. Optionally enable OpenAI mode with your server-side model/key. Verify tool selection/explanation while checking that figures remain exactly calculator-derived. Simulate an unavailable provider; warnings should disclose deterministic fallback. No external scraping or sales actions exist.

## Validation

Ruff lint/format checks passed. The final backend suite passed **367 tests**, including **16 focused recommendation-agent tests**. Frontend lint/type checking and all ten Node test files passed. The complete webpack production build passed with the temporary compiler-API workaround, and original Next configuration was restored. SQLite upgrade/check/downgrade/re-upgrade and PostgreSQL offline migration SQL succeeded. The configured live database remained unreachable (`OperationalError`), so no live PostgreSQL migration is claimed.

Backend commands:

```sh
.venv/bin/ruff check app tests migrations
.venv/bin/ruff format --check app tests migrations
GOVINENA_ADMIN_USER_IDS='[]' .venv/bin/python /tmp/govinena-pytest-runner.py -q
GOVINENA_ADMIN_USER_IDS='[]' .venv/bin/python /tmp/govinena-pytest-runner.py tests/test_recommendations.py -q
GOVINENA_ADMIN_USER_IDS='[]' DATABASE_URL=sqlite:////tmp/govinena-agent-migration.db .venv/bin/alembic upgrade head
GOVINENA_ADMIN_USER_IDS='[]' DATABASE_URL=sqlite:////tmp/govinena-agent-migration.db .venv/bin/alembic check
```

The temporary test runner only caps selector waits at 10 ms for this sandbox's TestClient cross-thread wakeups. Application code/tests are unchanged by that workaround; ordinary `pytest -q` is suitable outside the restricted runtime. Tests cover successful workflow, calculator agreement, tool order/selection, durable missing-input resume, stale/missing observations, route failure, LLM fallback, retry/step/time limits, unsupported tools/actions, checkpoint retention, approvals, ownership and unchanged legacy features. Authenticated Playwright was attempted but local server binding is blocked; no browser test is claimed as passed.

Native checkpoint/interrupt semantics were checked against the installed package and the [official LangGraph persistence documentation](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/persistence.mdx).
