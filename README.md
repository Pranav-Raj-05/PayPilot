# PayPilot — AI payment operations on PayPal

PayPilot is an AI copilot for freelancers and small businesses. Ask in plain English — *"Create a $500 invoice for Sarah Johnson for website development"* — review a structured preview, approve it once, and PayPilot creates the invoice and a **PayPal Sandbox** checkout link. Payments are verified server-side before anything is marked paid.

Built for the PayPal AI Hackathon. **PayPal (Orders v2, Sandbox) is the primary gateway.** Razorpay (INR, test mode) is an optional, fully separate secondary provider.

## Problem
Small businesses chase payments by hand: write an invoice, create a payment link, check whether it was paid, reconcile, follow up, refund. The work is repetitive and error-prone, and money mistakes (wrong amount, double capture, a late payment on a cancelled invoice) are expensive.

## Solution and key features
* **AI Copilot** — natural-language requests become typed tool calls (14 tools: invoices, customers, payment links, PayPal orders, transactions, revenue, pending/overdue, business analysis, reminder drafts). **Sensitive tools only *prepare* an action; nothing executes until the user approves it in the UI** (`prepared → approved → executed`). Conversations are persisted.
* **PayPal payments** — invoice → PayPal order → customer approves on PayPal → backend capture → amount/currency verification → ledger update. Captures are idempotent.
* **Money-integrity rules** — one live order per invoice; invoice edits are blocked while a payment link is live; cancelling an invoice voids its open orders and links; late or mismatched payments are recorded rather than silently accepted; partial refunds are tracked in a refunds ledger.
* **Webhooks** — PayPal webhook signatures are verified; processing is retry-safe and de-duplicated.
* **App** — Overview, AI Assistant, Transactions (+ lifecycle drawer), Invoices, Invoice editor, Payment Links (+QR), Customers, Analytics, Automations, Settings, auth (register / login / reset), protected routes. Figures come from the database; empty accounts show empty states.
* **Automations implemented** — daily revenue summary, failed-payment monitor, overdue-reminder suggestions (drafted for approval; **no email/SMS is sent**), event-driven payment notifications.
* **Security** — Supabase JWT verification, ownership-scoped data access, row-level security on all tables (financial tables read-only to clients), rate limiting, audit log, safe error responses, no secrets in the frontend.

## How PayPilot uses PayPal
* **PayPal Orders v2** against the Sandbox (`PAYPAL_BASE_URL`): create order → buyer approves on PayPal → capture on the backend.
* The backend **re-reads the captured order from PayPal and verifies amount and currency** against the invoice before marking anything paid.
* **Webhooks** (`CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED`) are verified with `PAYPAL_WEBHOOK_ID`.
* **Refunds** (full and partial) are issued through PayPal and recorded in PayPilot. Razorpay refunds are not implemented.

## How AI is integrated
* The backend agent (`backend/app/ai`) calls **Gemini or OpenAI with native function/tool calling** (`AI_PROVIDER=gemini|openai`).
* The model can only call registered, typed tools, each scoped to the signed-in user. Data-changing tools return a preview (`prepared_awaiting_user_approval`); execution happens only through the approval endpoint after the user confirms.
* **Requires your own `GEMINI_API_KEY` or `OPENAI_API_KEY`.** Without one the assistant reports that AI is not configured; the rest of the app still works.
* Replies arrive when the tool loop finishes (no token streaming).
* Default model `gemini-3.5-flash` (override with `GEMINI_MODEL`). A Google free-tier key allows only about **20 requests per day per model**, and one assistant turn uses at least two requests, so a free key supports roughly ten turns a day. Enable billing on the key (or switch `GEMINI_MODEL`) if you need more.

### AI tool-calling workflow (human approval before financial operations)
1. The user sends a message to `POST /api/ai/chat`; the backend sends it to Gemini together with the 14 tool declarations.
2. **Read tools** (list/get transactions, revenue, pending payments, analysis, reminder suggestions) run immediately, scoped to the signed-in user, and their results go back to the model.
3. **Write tools** (create/update invoice, create customer, generate a payment link, create a PayPal order, queue a reminder) are only **prepared**: the stored action has status `prepared` and the UI shows a preview. **No invoice is created and no PayPal request is made.**
4. The user clicks **Approve** (`POST /api/ai/actions/{id}/confirm`) or **Cancel** (`POST /api/ai/actions/{id}/cancel`). Only an approved action executes, and only once (a cancelled action cannot be confirmed afterwards).
5. Invalid tool arguments (for example a zero amount) are returned to the model as an error and are never executed.
* Verified by automated tests and by a live Gemini session during development: a transaction summary using `list_transactions`, an invoice prepared and then created only after approval, a payment-link action prepared and cancelled with no PayPal request, and no API key in any log record.

## Technology stack
React 18 · TypeScript · Vite · Tailwind CSS · TanStack Query · Python 3.12 · FastAPI · SQLAlchemy 2 · Supabase (Auth + Postgres) · PayPal Orders v2 (Sandbox) · Razorpay (optional) · Gemini / OpenAI · Vitest + React Testing Library · pytest.

## System architecture
```
Browser (React/Vite)                FastAPI                               External
 ├─ Supabase Auth (anon key) ─────► JWT (Bearer) ──► verify HS256/JWKS
 ├─ axios + TanStack Query ───────► /api/*  routes ─► services ─► repositories ─► Postgres (Supabase) / SQLite (local dev)
 ├─ Copilot chat ─────────────────► /api/ai/chat ──► Agent ──► LLM (Gemini/OpenAI, function calling)
 │                                                     └─► tool registry ─► services (user-scoped)
 └─ Customer checkout (no login) ─► /api/public/pay/* ─► PaymentService ─► PayPal Orders v2 / Razorpay
                                    /api/webhooks/*  ◄── PayPal / Razorpay (signature verified)
```
More detail: [docs/architecture.md](docs/architecture.md) · [API](docs/api-documentation.md).

## Application workflow
1. Sign in (Supabase Auth).
2. Create a customer and invoice — by form, or by asking the AI Assistant (it shows a preview; you approve).
3. Create a PayPal payment link for the invoice (shareable URL + QR).
4. The customer opens the link and pays with PayPal.
5. PayPilot captures the order, verifies amount and currency, and marks the transaction completed and the invoice paid.
6. The merchant sees the result in Transactions, Overview and Analytics; refunds can be issued from Transactions.

## Installation and local development
Requirements: Python 3.12, Node 18+, a Supabase project (Auth is required; Postgres or SQLite for data), PayPal Sandbox REST app credentials.

```bash
# Backend
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                 # fill in values (below)
uvicorn app.main:app --reload                        # http://localhost:8000/docs

# Frontend (second terminal)
cd frontend
cp .env.example .env
npm install
npm run dev                                          # http://localhost:5173
```
For a quick local run without Supabase Postgres set `DATABASE_URL=sqlite:///./dev.db`. Sign-in still uses Supabase Auth.

## Environment variables (placeholders only — never commit real values)
**backend/.env**
```
APP_ENV=development
FRONTEND_URL=http://localhost:5173
BACKEND_URL=http://localhost:8000
DATABASE_URL=<postgres connection string, or sqlite:///./dev.db>
SUPABASE_URL=<https://your-project.supabase.co>
SUPABASE_JWT_SECRET=<project JWT secret>
PAYPAL_CLIENT_ID=<sandbox client id>
PAYPAL_CLIENT_SECRET=<sandbox client secret>
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com
PAYPAL_WEBHOOK_ID=<sandbox webhook id>
AI_PROVIDER=gemini
GEMINI_API_KEY=<your key>            # or OPENAI_API_KEY with AI_PROVIDER=openai
GEMINI_MODEL=gemini-3.5-flash        # optional; set it if Google retires or overloads the default
RAZORPAY_KEY_ID=<optional>
RAZORPAY_KEY_SECRET=<optional>
RAZORPAY_WEBHOOK_SECRET=<optional>
```
**frontend/.env** (everything prefixed `VITE_` is shipped to the browser — public values only)
```
VITE_API_URL=http://localhost:8000
VITE_SUPABASE_URL=<https://your-project.supabase.co>
VITE_SUPABASE_ANON_KEY=<anon key>
```

## Database
SQL migrations are in `supabase/migrations/` and are applied in order: `init_schema`, `rls_policies`, then `money_integrity` (required by the current backend: invoice versioning, refunds table, `partially_refunded` status). They are tested on a local PostgreSQL 16.2 only; they have **not** been run against a live Supabase project by the automated checks, and PostgreSQL 17 (the Supabase version) has not been tested. See [docs/deployment.md](docs/deployment.md) and [docs/postgres-testing.md](docs/postgres-testing.md).

## Deployment
* Backend: [render.yaml](render.yaml) (Docker, health check `/health`). Frontend: `frontend/vercel.json` or `frontend/netlify.toml` (static Vite build with SPA rewrite).
* Set `VITE_API_URL` to the public backend URL, and `FRONTEND_URL` / `CORS_ORIGINS` on the backend to the public frontend URL.
* Full steps: [docs/deployment.md](docs/deployment.md).
* **Deployment status:** no public deployment is recorded in this repository.

## PayPal Sandbox testing
No real money is involved. Step by step: [docs/manual-testing.md](docs/manual-testing.md). In short:
1. developer.paypal.com → Apps & Credentials (Sandbox) → copy the app's client ID and secret into `backend/.env`.
2. Testing Tools → Sandbox Accounts → use the **personal (buyer)** account to pay.
3. In PayPilot: Invoices → create an invoice → *Create PayPal link* → open the checkout → log in with the sandbox buyer → pay.
4. You return to PayPilot's confirmation page; the transaction shows as completed and the invoice as paid.

## Tests
```bash
cd backend && pytest -q                    # PayPal/Razorpay/LLM calls are mocked; PostgreSQL tests skip unless configured
cd frontend && npm test && npm run build
```

## Screenshots
Screenshots are not committed yet. Add them to `docs/screenshots/` and link them here: Overview dashboard · AI Assistant with a prepared-action preview · Payment link · PayPal Sandbox checkout · Transactions with a completed payment.

## Limitations and future improvements
* **Sandbox only.** Live PayPal has not been used or verified.
* The AI assistant needs your own Gemini/OpenAI key and has no token streaming. On a free Gemini key the daily quota (about 20 requests per model per day) is easy to exhaust; the app then shows "The AI provider rate limit or quota was reached".
* **Reminders are drafted, not sent** — no email/SMS delivery.
* Razorpay is test-mode only and has no refunds.
* PayPal refund/reversal **webhook payload shapes have not been verified against live Sandbox events**.
* The Figma file had six frames (Overview, AI Assistant, Invoice Management, Transactions, Payment Links Hub, AI Execution Timeline); all six are implemented against real data. Other screens reuse the same tokens. Icons are Lucide and avatars are initials. Illustrative Figma values with no backend (simulated balance, "Fraud & Risk Guard", "Level 3", "Switch to Live", PayLater/Venmo toggles, resend-email, "Simulate webhook") were replaced with real data or disabled, not faked.
* Schema changes are forward-only (no down migration), and the migrations are untested on live Supabase and PostgreSQL 17.
* Planned: a readiness endpoint that checks the schema, a "Partially refunded" status tab, email delivery for reminders, a rollout runbook.

## License
MIT — see [LICENSE](LICENSE).
