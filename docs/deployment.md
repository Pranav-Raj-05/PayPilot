# Deployment

## 1. Supabase
1. Create a project. In **SQL editor** run `supabase/migrations/20260101000000_init_schema.sql`, then `20260101000100_rls_policies.sql`, then `20260102000000_money_integrity.sql` (or `supabase db push`). The last one adds invoice versioning, partial refunds (`refunds` table, `partially_refunded` status) and is required by the current backend. It replaces exactly one existing constraint, the CHECK on `transactions.status` (it is found by the column it constrains, not by its name, and the migration fails closed with a `money_integrity:` error, dropping nothing, if that CHECK is not the expected one); everything else it adds is new (columns, a `refunded_amount` CHECK, the `refunds` table). It is tested on vanilla PostgreSQL (see `docs/postgres-testing.md`) but has **not** been executed against a live Supabase project yet, so run it on a development branch/staging project first, following the pre-flight checklist in that guide (inspect first, apply files individually, back up or branch because it is forward-only, migrate before deploying the backend and re-run the backfill once after cutover). Optional lock-timeout override: `set money_integrity.lock_timeout = '30s';` before running the file.
2. Auth → URL configuration: Site URL = your frontend URL; add `<frontend>/login` and `<frontend>/reset-password` to redirect URLs. Keep email confirmation on.
3. Collect: Project URL, anon key (frontend), JWT secret (backend, Project Settings → API), and the Postgres connection string (backend `DATABASE_URL`; the pooler URL works too).
4. Optional demo data: `select seed_paypilot_demo('<auth-user-uuid>');` from `supabase/seed.sql`.

## 2. PayPal sandbox
1. developer.paypal.com → Apps & Credentials → Sandbox → create app → copy Client ID / Secret.
2. Create a webhook pointing to `<BACKEND_URL>/api/webhooks/paypal` with events `CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED`; copy the Webhook ID into `PAYPAL_WEBHOOK_ID`.

## 3. Backend → Render
`render.yaml` is a Blueprint (Docker, health check `/health`). Create it from the repo and fill the `sync: false` env vars (see `.env.example`). Set `FRONTEND_URL` to the deployed frontend so CORS and PayPal return URLs are right. Equivalent without Docker: root `backend`, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

## 4. Frontend → Vercel *or* Netlify (one is enough)
Root directory `frontend`, build `npm run build`, output `dist`. `vercel.json` / `netlify.toml` add the SPA rewrite and security headers. Env vars: `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` (public values only – never put secrets in `VITE_*`).

## Rate limiting and reverse proxies
Authenticated endpoints are limited **per user**; public checkout endpoints are limited **per client IP**. The limiter is in memory (per process): run a single instance, or move it to Redis before scaling out.

Forwarded headers are never trusted by default. Set `TRUSTED_PROXY_COUNT` to the number of reverse proxies that sit in front of the API and each append to `X-Forwarded-For`:

| Setup | `TRUSTED_PROXY_COUNT` |
|---|---|
| Local / directly exposed | `0` (default): the TCP peer address is used, `X-Forwarded-For` is ignored |
| Render (one proxy in front) | `1` (already set in `render.yaml`) |
| CDN/WAF **plus** the platform proxy | `2`, and so on |

The client address is the N-th entry from the **right** of `X-Forwarded-For` (the address the outermost trusted proxy saw); entries to its left are client-supplied and ignored. If the count is too low, every visitor appears to share one address (a shared quota: an availability problem, not a security one); too high and the header is ignored. Verify after deploying: send two requests from different networks and check that `/api/public/pay/...` limits them separately. The container runs uvicorn with `--no-proxy-headers` on purpose so client-address handling lives in one place (`app/core/ratelimit.py`).

## Environment variables
| Var | Where | Purpose |
|---|---|---|
| `DATABASE_URL` | backend | Supabase Postgres connection |
| `SUPABASE_URL`, `SUPABASE_JWT_SECRET` | backend | JWT verification (HS256 secret or JWKS) |
| `PAYPAL_CLIENT_ID/SECRET/BASE_URL/WEBHOOK_ID` | backend | PayPal |
| `RAZORPAY_KEY_ID/KEY_SECRET/WEBHOOK_SECRET` | backend | Razorpay (optional) |
| `AI_PROVIDER`, `GEMINI_API_KEY` or `OPENAI_API_KEY` | backend | LLM |
| `FRONTEND_URL`, `BACKEND_URL`, `CORS_ORIGINS` | backend | URLs/CORS |
| `TRUSTED_PROXY_COUNT` | backend | reverse proxies in front of the API (see above), default `0` |
| `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | frontend | public config |

`SUPABASE_SERVICE_ROLE_KEY` is intentionally **not used**: the API talks to Postgres directly, so that key never needs to exist outside the Supabase dashboard.

## 5. Verify
`GET <backend>/health` → ok · `/docs` loads · sign up/in · Settings shows PayPal “Connected” · follow `docs/manual-testing.md`.
