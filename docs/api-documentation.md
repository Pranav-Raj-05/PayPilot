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
| Transactions | `GET /api/transactions?search&status&provider&sort&order&page&page_size`, `GET /api/transactions/{id}` |
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
