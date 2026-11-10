# Deployment

## 1. Supabase
1. Create a project. In **SQL editor** run `supabase/migrations/20260101000000_init_schema.sql`, then `20260101000100_rls_policies.sql` (or `supabase db push`).
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

## Environment variables
| Var | Where | Purpose |
|---|---|---|
| `DATABASE_URL` | backend | Supabase Postgres connection |
| `SUPABASE_URL`, `SUPABASE_JWT_SECRET` | backend | JWT verification (HS256 secret or JWKS) |
| `PAYPAL_CLIENT_ID/SECRET/BASE_URL/WEBHOOK_ID` | backend | PayPal |
| `RAZORPAY_KEY_ID/KEY_SECRET/WEBHOOK_SECRET` | backend | Razorpay (optional) |
| `AI_PROVIDER`, `GEMINI_API_KEY` or `OPENAI_API_KEY` | backend | LLM |
| `FRONTEND_URL`, `BACKEND_URL`, `CORS_ORIGINS` | backend | URLs/CORS |
| `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | frontend | public config |

`SUPABASE_SERVICE_ROLE_KEY` is intentionally **not used**: the API talks to Postgres directly, so that key never needs to exist outside the Supabase dashboard.

## 5. Verify
`GET <backend>/health` → ok · `/docs` loads · sign up/in · Settings shows PayPal “Connected” · follow `docs/manual-testing.md`.
