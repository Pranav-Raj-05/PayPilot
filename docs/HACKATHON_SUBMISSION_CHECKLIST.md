# PayPilot — PayPal AI Hackathon submission checklist

Fill the `<...>` items yourself. Everything else below is accurate for the code in this repository.

## Required items

| Item | Status | Value / action |
|---|---|---|
| Project title | ready | **PayPilot — AI Payment Agent** |
| Short description | ready | *(below)* |
| Full description | ready | *(below)* |
| GitHub repository URL | ready, **but see warning** | https://github.com/Pranav-Raj-05/PayPilot |
| Public demo URL | **missing** — no deployment found | `<paste your deployed frontend URL>` or state "local demo, see video" |
| Demo video URL | **missing** | `<paste YouTube/Vimeo/Loom URL>` (under 3 minutes; script: `docs/HACKATHON_DEMO_SCRIPT.md`) |
| README with setup instructions | ready (uncommitted) | `README.md` |
| License | ready | MIT (`LICENSE`) |
| Screenshots | **missing** | add to `docs/screenshots/` |

> **Warning — the work is not in GitHub yet.** At the time of writing, `origin/claude/keen-cray-j1vht5` is at commit `9a4631a` and none of the current work (including this README and these docs) is committed or pushed. Judges who open the repository will not see it until you commit and push (and, if the default branch is different, merge or push to it). Check which branch the hackathon form expects.

## Short description (≈ 300 characters)
PayPilot is an AI payment agent for freelancers and small businesses. Describe an invoice in plain English, approve the AI's prepared action, and PayPilot creates a PayPal checkout link, verifies the Sandbox payment server-side, and tracks the result and refunds in one dashboard.

## Full description
**Problem.** Small businesses spend hours creating invoices, sending payment links, checking who has paid, and reconciling. Money mistakes — wrong amounts, double captures, payments against cancelled invoices — are costly.

**Solution.** PayPilot combines an AI copilot with PayPal Checkout. The user asks in natural language; the AI prepares a structured action (for example, an invoice); the user approves it; PayPilot creates a PayPal order and shareable link; the customer pays; the backend captures and verifies the payment with PayPal before marking anything paid. A dashboard shows invoices, payment links, transactions, analytics and refunds.

**PayPal integration.** PayPal Orders v2 in the Sandbox: create order, buyer approval, server-side capture, re-read of the captured order to verify amount and currency, idempotent captures, signature-verified webhooks (`CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.CAPTURE.REFUNDED`), and full/partial refunds recorded in a refunds ledger. Money-integrity rules: one live order per invoice, edits blocked while a link is live, cancelling an invoice voids open orders and links.

**AI integration.** A backend agent calls Gemini or OpenAI with native function calling over 14 typed, user-scoped tools (invoices, customers, payment links, PayPal orders, transactions, revenue, pending payments, analysis, reminder drafts). Data-changing tools only *prepare* a preview; execution requires explicit user approval. Requires the operator's own API key.

## Technologies used
React 18, TypeScript, Vite, Tailwind CSS, TanStack Query, Python 3.12, FastAPI, SQLAlchemy 2, Supabase (Auth, Postgres, row-level security), PayPal Orders v2 (Sandbox), Razorpay (optional, test mode), Google Gemini and/or OpenAI API, Render (backend config) and Vercel/Netlify (frontend config), Vitest, React Testing Library, pytest, GitHub Actions.

## AI tools and third-party services disclosure
- **AI used in development:** Claude / Claude Code (Anthropic) assisted with development, testing and documentation. `<confirm and add any other AI tools you used>`
- **AI used at runtime:** Google Gemini or OpenAI (selected by `AI_PROVIDER`) for the assistant. `<state which one you demo>`
- **Third-party services:** PayPal Developer Sandbox, Supabase, Razorpay (optional, test mode), Render / Vercel / Netlify only if you deploy.
- **Open-source libraries:** see `backend/requirements.txt` and `frontend/package.json`.

## Known limitations (state these honestly)
- Sandbox only; live PayPal has not been used.
- The AI assistant requires an operator-supplied Gemini/OpenAI key, and has no token streaming. A free Gemini key is limited to about 20 requests per model per day, which the assistant reports as a quota error when exhausted.
- Payment reminders are drafted for approval; **no email/SMS is sent**.
- Razorpay is test-mode only with no refunds.
- PayPal refund/reversal webhook payload shapes were not verified against live Sandbox events.
- The SQL migrations were tested on a local PostgreSQL 16.2 only, not on a live Supabase project or PostgreSQL 17.
- Some Figma-design placeholders were replaced or disabled rather than faked (see README).
- No public deployment is recorded in the repository.

## Verified in this repository (by actual runs, 2026-11-12)
- Backend tests: `pytest tests` — **440 passed, 62 skipped** (the skipped tests are the PostgreSQL tests, which skip without a configured PostgreSQL server).
- Frontend tests: `npm test` — **19 files, 438 tests passed**.
- Production build: `npm run build` — **succeeded**.
- Backend starts and serves: `/health` 200, `/docs` 200, protected routes return 401 without a token, CORS allows the dev origin (checked on a scratch SQLite database).
- Frontend dev server starts and serves HTTP 200.
- AI assistant, verified by a live Gemini session earlier in development (model `gemini-3.5-flash`) and by 27 automated AI tests: transaction summary via `list_transactions`, invoice prepared and created only after approval, payment-link action prepared and cancelled with no PayPal request, no key in logs. Not re-verified live on the final run because the free-tier daily quota was exhausted (HTTP 429).
- **Not verified by me:** the PayPal Sandbox payment flow end to end (it needs your authenticated session and the PayPal buyer login; you reported it working), the AI assistant with a real LLM key, and any deployment.

## Manual actions before submitting
1. Decide which branch the form expects; commit and push the work (README, docs and code) with your approval.
2. Record the demo video (script in `docs/HACKATHON_DEMO_SCRIPT.md`) and upload it.
3. Add 4–5 screenshots to `docs/screenshots/`.
4. Deploy (optional but strongly recommended) or state clearly that the demo is local.
5. Confirm the AI key is set if you show the AI Assistant.
6. Make sure the repository is public (or shared with the judges as the rules require) and contains no secrets (`.env` files are git-ignored).
7. Paste the URLs into the form.
