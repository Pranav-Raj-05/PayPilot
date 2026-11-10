# PayPilot — AI payment operations on PayPal

PayPilot is an AI copilot for freelancers and small businesses. Ask in plain English — *“Create a $500 invoice for Sarah Johnson for website development”* — review a structured preview, approve it once, and PayPilot creates the invoice and a real **PayPal Sandbox** checkout link. Payments are verified server‑side before anything is marked paid.

Built for the PayPal AI Hackathon. **PayPal is the primary gateway**; Razorpay (INR, test mode) is an optional, fully separate secondary provider.

## What works
* **Copilot** (Gemini or OpenAI function calling): 14 typed tools — invoices, customers, payment links, PayPal orders, transactions, revenue, pending/overdue, business analysis, reminder drafts. Sensitive tools only *prepare*; the user approves in the UI (`prepared → approved → executed`). Conversations persisted.
* **PayPal Orders v2**: create order → customer approval → backend capture → amount/currency verification → ledger update; idempotent captures; webhook signature verification via PayPal; retry-safe webhook processing; refunds.
* **Razorpay**: orders, HMAC signature verification + payment re-fetch, webhooks.
* **App**: Overview, AI Assistant, Transactions (+ lifecycle drawer), Invoices, Invoice editor, Payment Links (+QR), Customers, Analytics, Automations, Settings, auth (register / login / reset), protected routes. All figures come from the database; empty accounts show empty states.
* **Automations actually implemented**: daily revenue summary, failed-payment monitor, overdue reminder suggestions (drafted for approval — **no email/SMS is sent**), event-driven payment notifications.
* **Security**: Supabase JWT verification, ownership-scoped repositories, RLS on all tables (financial tables read-only to clients), rate limiting, audit log, safe error responses, no secrets in the frontend.

## Quick start
```bash
# 1. Database: run supabase/migrations/*.sql in a Supabase project (or use sqlite locally: DATABASE_URL=sqlite:///./dev.db)
# 2. Backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # fill in values
uvicorn app.main:app --reload            # http://localhost:8000/docs
# 3. Frontend
cd ../frontend && cp .env.example .env && npm install && npm run dev   # http://localhost:5173
```
Tests: `cd backend && pytest -q` (59 tests, providers mocked) · `cd frontend && npm test && npm run build`.

## Docs
[Architecture](docs/architecture.md) · [API](docs/api-documentation.md) · [Deployment](docs/deployment.md) · [Manual sandbox testing](docs/manual-testing.md)

## Design fidelity & honesty notes
* The Figma file contains two designed screens (Overview, AI Assistant) — those follow it closely (tokens, layout, copy, components). Other pages reuse the same tokens but are **not** from Figma.
* Figma's exported SVG/photo assets sit on a host the build environment could not reach, so icons are Lucide and avatars are initials.
* Figma showed illustrative values (simulated balance, “Fraud & Risk Guard”, “Level 3”, “Switch to Live”, attachments/mic). Anything without a real backend was replaced with real data or disabled rather than faked.
* No AI token streaming; AI replies arrive when the tool loop finishes.
* The SQL migrations were syntax-checked with a Postgres parser but not executed against a live Supabase project in this build, and live PayPal/Razorpay/LLM calls were verified only against mocks — run `docs/manual-testing.md` with real sandbox keys before the demo.

MIT licensed.
