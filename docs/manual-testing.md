# Manual PayPal Sandbox test (no real money)

Prereqs: backend + frontend running with sandbox credentials; a PayPal **sandbox personal (buyer) account** from developer.paypal.com → Testing Tools → Sandbox Accounts.

1. Register, verify email, sign in. Settings → PayPal should show **Connected** with latency.
2. **Happy path (UI):** Invoices → Create Invoice ($25) → *Create PayPal link* → Open checkout → log in with the sandbox buyer → Pay. You land on `/pay/return` showing “Payment received”. Invoice → **paid**; Transactions shows the timeline `order_created → captured → invoice_paid`.
3. **Happy path (AI):** Assistant → “Create a $500 invoice for Sarah Johnson for website development”. Review the staged card → *Confirm & Create on PayPal* → copy/scan the link and pay as above.
4. **Cancel:** open a link, click “Cancel and return” at PayPal → `/pay/cancelled`; transaction becomes `cancelled`, a later capture attempt is refused.
5. **Duplicate capture:** reload `/pay/return?token=<order id>` after paying – status stays completed, no second capture (check Transactions events).
6. **Webhook:** with the webhook configured (use a tunnel such as ngrok for local), pay an invoice; Settings → webhooks shows “Receiving events”; PayPal dashboard → webhook → *Resend* an event: response is `duplicate: true`.
7. **Negative:** `curl -X POST <api>/api/webhooks/paypal -d '{}'` → 401. Calling `capture-order` with another user's token → 404.
8. **Razorpay (optional, INR):** create an INR invoice → *Create Razorpay link* → pay with test UPI `success@razorpay` / test card `4111 1111 1111 1111`.

Automated tests (mocked providers): `cd backend && pip install -r requirements-dev.txt && pytest -q` and `cd frontend && npm test`.
