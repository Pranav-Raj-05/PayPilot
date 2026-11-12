# API

Interactive docs: `GET /docs` (Swagger) and `/openapi.json`. All `/api/*` routes except `/api/public/*` and `/api/webhooks/*` need `Authorization: Bearer <Supabase access token>`.

Errors: `{"error": {"code": "...", "message": "...", "details": null}}` with proper HTTP status (401/403/404/409/422/429/502/503).
Lists: `{"items": [], "total": 0, "page": 1, "page_size": 50}`.

| Area | Endpoints |
|---|---|
| Health | `GET /health` |
| Auth | `GET /api/auth/me` (auth itself is Supabase) |
| Settings | `GET /api/settings`, `PATCH /api/settings/profile` |
| Dashboard / Analytics | `GET /api/dashboard?days&currency`, `GET /api/analytics?days&currency` |
| Customers | `GET/POST /api/customers`, `GET/PATCH/DELETE /api/customers/{id}` |
| Invoices | `GET/POST /api/invoices`, `GET/PATCH/DELETE /api/invoices/{id}` (delete = drafts only), `POST /api/invoices/{id}/payment-link?provider=paypal|razorpay` |
| Transactions | `GET /api/transactions?search&status&provider&sort&order&page&page_size`, `GET /api/transactions/{id}` (includes `refunded_amount` and the `refunds` history), `POST /api/transactions/{id}/refund` |
| Payment links | `GET/POST /api/payment-links`, `POST /api/payment-links/{id}/cancel` |
| **PayPal** | `POST /api/payments/paypal/create-order`, `POST /api/payments/paypal/capture-order`, `GET /api/payments/paypal/order/{order_id}`, `GET /api/payments/paypal/status` |
| **Razorpay** | `POST /api/payments/razorpay/create-order`, `POST /api/payments/razorpay/verify-payment` |
| Webhooks | `POST /api/webhooks/paypal`, `POST /api/webhooks/razorpay` |
| Public checkout | `GET /api/public/pay/link/{id}`, `POST /api/public/pay/paypal/capture|cancel`, `POST /api/public/pay/razorpay/verify` |
| AI | `POST /api/ai/chat`, `GET /api/ai/conversations`, `GET/DELETE /api/ai/conversations/{id}`, `GET /api/ai/actions`, `POST /api/ai/actions/{message_id}/confirm|cancel` |
| Automations | `GET /api/automations`, `PATCH /api/automations/{id}`, `POST /api/automations/{id}/run` |
| Notifications | `GET /api/notifications`, `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all` |

### PayPal flow
1. `POST /api/payments/paypal/create-order {invoice_id}` → PayPal order (intent CAPTURE) + `approval_url` (payer-action link) and a `payment_links` row.
2. Customer opens the link, approves at PayPal, and is redirected to `FRONTEND_URL/pay/return?token=<order_id>`.
3. That page calls the public capture endpoint → backend captures, **verifies amount/currency**, then marks transaction + invoice. The `PAYMENT.CAPTURE.COMPLETED` webhook is an independent confirmation/backstop.

### Refunds
`POST /api/transactions/{id}/refund` with an optional JSON body `{"amount": 25.00, "reason": "...", "idempotency_key": "..."}` (the key may also be sent as an `Idempotency-Key` header).

* No `amount` = refund **everything still refundable** (the original amount minus completed and in-flight refunds).
* `amount` must be between 0.01 and what is left, otherwise `422`. Nothing left and an amount given: `409`.
* The payment keeps its original `amount`; `refunded_amount` is the sum of completed refunds. Status: `completed` → `partially_refunded` → `refunded`. A full refund returns the invoice to `pending`; a partial refund never reopens it.
* A repeated `idempotency_key` returns the first result and never refunds twice. Every refund is one row in `refunds` (unique per provider refund id), so duplicate or out-of-order webhooks cannot double count it.
* PayPal only for now (`400 unsupported` for Razorpay payments); the refund core is provider-agnostic and Razorpay plugs in through `PaymentService._refunders`.
* `PAYMENT.CAPTURE.REFUNDED` webhooks are verified, then the refund is **read back from PayPal** (amount and currency are never taken from the webhook body). Refunds created in the PayPal dashboard are therefore picked up correctly, including partial ones. A refund that is in another currency or larger than the payment is not applied; it is flagged on the transaction and in notifications.

### Invoice edits and payment links
While an invoice has a live payment link, its content (items, tax, currency, customer, description, due date) cannot be edited and it cannot go back to draft: cancel the link first (`POST /api/payment-links/{id}/cancel`), which also cancels the order behind it. Cancelling an invoice cancels its links and orders the same way. An invoice has a `version` that increases when anything a payment depends on changes; an order only pays the invoice version it was created for.
