# Feature 01: authentication and sessions

Both repositories remain on `feature/auth`. No merge or remote push was performed.

## Configure

1. Create a Supabase project (or use your organization's existing project). Enable email/password authentication and email confirmation. Configure SMTP for reliable confirmation and recovery emails. No service role key or JWT signing secret is needed by either application.
2. Add `GOVINENA_SUPABASE_URL` and `GOVINENA_SUPABASE_PUBLISHABLE_KEY` to the backend `.env`. Use the project's URL and publishable key (legacy anon key is also supported). Keep `GOVINENA_SUPABASE_AUDIENCE=authenticated` unless your provider configuration requires otherwise.
3. Use PostgreSQL for production through the existing `DATABASE_URL` setting. SQLite remains supported for local development. Run `.venv/bin/alembic upgrade head` from the backend directory before starting the API with `.venv/bin/uvicorn app.main:app --reload`.
4. Follow the frontend's `AUTHENTICATION.md` for cookies, frontend environment variables, email templates, and UI checks.

## API and storage

| Endpoint | Access | Behavior |
| --- | --- | --- |
| `GET /api/v1/users/me` | Authenticated, confirmed email | Creates or links profile by verified provider UUID; returns `id`, `email`, `created_at`. Never accepts a caller-selected identity. |
| `POST /api/v1/auth/logout` | Provider-verified identity | Idempotently revokes the current session. Frontend then signs out of Supabase with local scope to invalidate its refresh token. |
| `GET /api/v1/markets` | Active session | Existing synthetic demo behavior preserved. |
| `POST /api/v1/agent/demo` | Active session | Existing demo behavior preserved. |
| `/health`, `/api/v1/health` | Public | Unchanged. |

Errors use the existing `{ "error": { "code", "message", "details" } }` envelope. Missing/invalid/expired credentials and revoked sessions return 401 with `WWW-Authenticate: Bearer`. An authenticated identity with an unconfirmed email or wrong role returns 403. Provider network errors, rate limits, and unavailable auth configuration return 503. Unexpected database failures use the existing safe 500 handler. User responses are `Cache-Control: no-store`.

Migration `a01_auth_users_sessions` follows `df69e3c200c8`. It creates `users` (provider UUID primary key, email, creation time) and `revoked_sessions` (session UUID primary key, revocation time). PostgreSQL tables enable row-level security without browser-role policies, so Supabase Data API roles cannot read these backend-owned tables. Use the owning server database role (or a dedicated backend role with an appropriate server-only policy); never use anon/authenticated roles as the backend connection. Email is not used as an identity key. There are no farmer-specific fields or passwords in this database. Revocations intentionally have no expiry: they also reject refreshed tokens with the same session ID. Do not purge them while a refresh token for that session could still exist.

Supabase's `/auth/v1/user` endpoint validates the token online, including signature and expiry. Only after a successful response does the API read claims, require the configured issuer and audience, check expiry/not-before, match subject to the provider response, and validate UUIDs and the authenticated role. Tokens are never accepted based on decoding alone. Online verification supports Supabase's signing-key changes and legacy projects without putting a signing secret in this app. An Auth outage fails closed.

App logout revokes the current session immediately in the shared application database. Other devices keep their own sessions. Logout performed outside this application follows Supabase's access-token expiry semantics; app-local revocation does not revoke direct access to unrelated Supabase services. No direct database access from the frontend is introduced by this feature.

## Tests and manual API verification

Run `.venv/bin/ruff check app tests migrations` and `.venv/bin/pytest -q` from this directory. Tests mock the provider HTTP boundary, not the cryptography. They cover missing/malformed credentials, provider signature rejection, issuer/audience/expiry/not-before claims, unconfirmed emails, role rejection, outages, profile creation, repeat requests, two-user isolation, and session revocation across refreshed tokens. Existing graph and API tests remain; demo tests explicitly override authentication, while authentication tests exercise real dependencies.

For a real provider check:

1. Sign in as A through the frontend. Confirm `GET /api/users/me` returns A's profile and refresh retains it.
2. Open a private browser, register and confirm B, then sign in. B's `/api/users/me` must return a different UUID and email.
3. Without an Authorization header, call `GET http://localhost:8000/api/v1/users/me`, `/markets`, and `POST /agent/demo` (prefix `/api/v1` on each). Expect 401.
4. In a local API client, obtain disposable test tokens directly from your test Supabase project's password grant. Send each token only to the configured API as `Authorization: Bearer <token>`. A's `/users/me?user_id=<B>` must still return A; `/users/<B>` must be 404.
5. Call `POST /api/v1/auth/logout` using A's token; retry A's `/users/me` with that same token and expect 401. B must still work. Never paste real tokens into reports or source files.
6. Try an expired token and a token with a changed signature; expect 401. Stop Supabase or point a disposable environment at an unreachable endpoint; authenticated requests must fail closed with 503.

## Validation in this workspace

- Ruff: passed.
- Backend tests: 60 passed using `/tmp/govinena-pytest-sandbox.py -q` with the virtualenv Python. Plain pytest stalled in TestClient startup; a bare FastAPI/TestClient reproduction also stalled. The temporary runner caps selector sleep at 10ms to compensate for blocked cross-thread loop wakeups in this sandbox. It does not change app code, test assertions, or mock authentication globally.
- Alembic: SQLite upgrade to head, schema check, downgrade to base, and re-upgrade all passed on a disposable database. Local configured SQLite database was upgraded. PostgreSQL migration SQL generated; no PostgreSQL server was available for a live migration run.
- Provider integration remains unverified: no Supabase project credentials or SMTP configuration were supplied, and external DNS is unavailable here.
