# PayPilot Architecture

```
Browser (React/Vite)                FastAPI (Render)                      External
 ├─ Supabase Auth (anon key) ─────► JWT (Bearer) ──► verify HS256/JWKS
 ├─ axios + TanStack Query ───────► /api/*  routes ─► services ─► repositories ─► Supabase Postgres
 ├─ Copilot chat ─────────────────► /api/ai/chat ──► Agent ──► LLM (Gemini/OpenAI, function calling)
 │                                                     └─► tool registry ─► services (user-scoped)
 └─ Customer checkout (no login) ─► /api/public/pay/* ─► PaymentService ─► PayPal Orders v2 / Razorpay
                                    /api/webhooks/*  ◄── PayPal / Razorpay (signature verified)
```

## Layers (backend/app)
| Layer | Responsibility |
|---|---|
| `api/routes` | HTTP only: validation (Pydantic), auth dependency, rate limits |
| `services` | Business rules: invoices, analytics, payments, automations |
| `repositories` | `OwnedRepository` – every query is filtered by `user_id` |
| `payments/paypal`, `payments/razorpay` | Provider clients, completely separate credentials and verification |
| `ai` | Provider adapters, typed tool registry, agent loop, approval state machine |
| `webhooks` (`api/routes/webhooks.py`) | Verified, de-duplicated provider events |

## Key design decisions
* **Identity** comes only from the verified Supabase JWT; `user_id` in request bodies is ignored.
* **DB access**: the API connects with the Postgres owner role (bypasses RLS) and enforces ownership in `OwnedRepository`. RLS is enabled on every table as defence in depth for any PostgREST path; financial tables (`transactions`, `payment_links`, `audit_logs`) are read-only to clients and a trigger forbids clients from marking invoices `paid`.
* **Payments** (`services/payments.py`): amount/currency come from the invoice in the DB; success is recorded only from a provider-verified capture; capture is idempotent (row lock + `PayPal-Request-Id` + terminal-status short-circuit); amount/currency mismatch marks the transaction failed and never pays the invoice; cancelled/expired links cannot be captured.
* **Webhooks**: PayPal via `verify-webhook-signature`, Razorpay via HMAC; events are stored in `webhook_events` (unique per provider+event id) so retries are no-ops; `PAYMENT.CAPTURE.COMPLETED` is re-verified with `GET /v2/checkout/orders/{id}`; a processing error rolls back (incl. the dedupe row) so the provider's retry reprocesses.
* **AI safety**: the LLM only sees tool schemas. Read tools run immediately (user-scoped). Sensitive tools (`create_invoice`, `generate_payment_link`, `create_paypal_order`, `create_customer`, `update_invoice`, `queue_payment_reminder`) only *prepare* an operation → stored in `ai_messages` as `prepared`. Execution only happens via `POST /api/ai/actions/{id}/confirm` by the owner. States: `prepared → approved → executed | failed`, or `prepared → cancelled`.
* **Automations** run in an in-process asyncio scheduler (`SCHEDULER_INTERVAL_SECONDS`) and write notifications; they never send email/SMS. On multi-instance deployments run a single scheduler instance or move `run_due` to a cron job.
* **Dashboard numbers** are computed from rows (`services/analytics.py`); empty accounts show zeros/“—”, never placeholder data.

## Money-integrity rules (enforced in `services/payments.py`, `services/invoices.py`, `services/orders.py`)
* **One live order per invoice.** A live order is a created/pending transaction whose link is active and unexpired. Creating a link reuses it (same provider), refuses the other provider, and voids a stale one.
* **Edits are blocked while an order is live**; cancelling an invoice or a link voids the order(s) with it. Orders remember `invoice_version`; capture is refused **before any money moves** if the invoice was cancelled, is already paid, changed version, amount or currency, or the link does not match the order.
* **A payment that arrives anyway** (webhook, Razorpay) is recorded as completed, because the money really moved, but never marks a cancelled/changed/already-paid invoice paid: it gets a `payment_unmatched` event and an alert notification instead.
* **Lock order** is transaction, then invoice, then link, everywhere, so a capture and a concurrent cancel/edit serialize on PostgreSQL row locks (`SELECT ... FOR UPDATE`) and exactly one wins. SQLite (dev/tests) has no row locks; the guarantee is asserted by the sequential tests and by a PostgreSQL-only race test (`TEST_POSTGRES_URL`, see `docs/postgres-testing.md`).
* **Refunds** are append-only rows with cumulative totals on the payment; see the API doc. Webhook refunds are verified with PayPal, idempotent per refund id and rejected-for-retry if the payment is not settled yet.
* **Notifications**: the "Payment notifications" automation switch controls payment received/failed/refunded notifications. Discrepancy alerts (amount mismatch, unmatched payment, refund anomalies) are never suppressed.
* **Rate limits** are per user for authenticated routes and per client IP for public ones (`app/core/ratelimit.py`); see docs/deployment.md for proxy settings.

## Known limitations
* PostgreSQL behaviour (migrations, row locks, the cancel/capture race) is verified on vanilla PostgreSQL only; Supabase-specific behaviour (real auth schema/JWT claims, PostgREST RLS, role grants, pooler transaction mode, migration runner) is not yet verified. See `docs/postgres-testing.md`.
* No streaming of AI tokens (responses return when the tool loop completes).
* In-memory rate limiter (per instance) and in-process scheduler.
* Refunds: PayPal only; a full refund returns the invoice to `pending` (a deliberate carry-over of the earlier behaviour, revisit if you want a `refunded` invoice status). No refund UI for choosing a partial amount yet (API and webhooks support it).
* Totals are per currency (no FX conversion).
* Reminders are recorded in-app only; no email/SMS provider is wired.
* Figma defines only the Overview and AI Assistant screens; all other screens extend the same design tokens.
