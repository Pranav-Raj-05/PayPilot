# PayPilot — Demo video script (target 2:40, hard limit 3:00)

Everything shown must be real. **Do a dry run first** and cut any segment that fails; do not narrate a feature you did not see work on screen.

## Pre-flight checklist (before you hit record)
- [ ] Backend and frontend running (or the deployed URLs open). `http://localhost:8000/health` returns `{"status":"ok"...}`.
- [ ] Signed in to PayPilot with a demo account; one customer exists (e.g. "Sarah Johnson").
- [ ] PayPal **Sandbox** buyer account credentials ready (developer.paypal.com → Testing Tools → Sandbox Accounts). Do not show the secret on screen.
- [ ] **Gemini quota:** a free key allows only about 20 requests per day per model and each assistant turn uses 2 or more. Do not rehearse the AI segment repeatedly; record it once or twice. If you see "rate limit or quota was reached", wait, set `GEMINI_MODEL` to another model (for example `gemini-3.1-flash-lite`), or enable billing.
- [ ] The key must be in **`D:\PayPilot-clone-temp\backend\.env`** (the folder the running backend uses) and the backend restarted.
- [ ] **AI check:** open the AI Assistant and send "list my customers". If it answers, the AI segment is real. If it says AI is not configured, set `GEMINI_API_KEY` (or `OPENAI_API_KEY`) in `backend/.env` and restart the backend, or **skip segment 2 and do not mention AI as a working feature**.
- [ ] Browser zoom 110–125%, notifications off, `.env` files and the PayPal dashboard's secret page not visible, no real personal data on screen.

## Script

| Time | On screen | Say (adapt to your own words) |
|---|---|---|
| 0:00–0:15 | PayPilot Overview dashboard | "Freelancers and small businesses lose hours writing invoices, sending payment links and checking who paid. PayPilot is an AI payment agent built on PayPal that does that work, with a human approval on every action." |
| 0:15–0:55 | **AI Assistant** *(only if the AI check passed)*. Type: "Create a $25 invoice for Sarah Johnson for website design". Show the preview card, then click **Approve**. | "I ask in plain English. The AI calls typed tools, but it can only *prepare* the invoice. Nothing is created until I approve it. Approved. The invoice now exists." |
| 0:55–1:15 | Invoices page → open the new invoice | "Here is the invoice, created by the assistant. Totals and status are stored in the database." *(If you skipped the AI: create a $25 invoice with the form instead and say so.)* |
| 1:15–1:35 | Click **Create PayPal link** → show the link/QR | "PayPilot creates a PayPal order for this exact amount and gives me a shareable link and QR code." |
| 1:35–2:05 | Open the link → PayPal Sandbox checkout → log in with the **sandbox buyer** → **Pay** | "This is PayPal's Sandbox checkout, so no real money moves. The customer approves the payment on PayPal." |
| 2:05–2:25 | Return page confirming payment | "Back in PayPilot the backend captures the order and re-checks the amount and currency with PayPal before it marks anything paid." |
| 2:25–2:45 | Transactions page → the completed payment → open the lifecycle drawer. Then the invoice showing **Paid** | "The transaction is completed and the invoice is paid. PayPilot also records refunds, handles webhooks and blocks edits to an invoice with a live payment link, so the books stay correct." |
| 2:45–3:00 | Overview / Analytics | "PayPilot: an AI agent for PayPal payments, with humans in control. Thank you." |

## Do not say
- Do not say reminders are *sent*: they are drafted for approval only; no email/SMS is delivered.
- Do not claim live PayPal payments, Razorpay refunds, or that the migrations were verified on Supabase.
- Do not claim real-time AI streaming.
- Do not show or read out any key, secret, token or `.env` content.

## If something fails while recording
- AI not configured → skip segment 2 (see pre-flight).
- Checkout page does not load → confirm `PAYPAL_BASE_URL` is the Sandbox URL and the Sandbox keys are in `backend/.env`; restart the backend.
- Return page does not show paid → open Transactions; if the order is pending, the PayPal webhook may not be configured (a local backend cannot receive webhooks unless tunnelled), but the in-app capture on return should still complete it.
