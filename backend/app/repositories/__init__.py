from app.models import Customer, Invoice, PaymentLink, Transaction
from app.repositories.base import OwnedRepository


class CustomerRepo(OwnedRepository[Customer]):
    model = Customer
    label = "Customer"


class InvoiceRepo(OwnedRepository[Invoice]):
    model = Invoice
    label = "Invoice"


class TransactionRepo(OwnedRepository[Transaction]):
    model = Transaction
    label = "Transaction"


class PaymentLinkRepo(OwnedRepository[PaymentLink]):
    model = PaymentLink
    label = "Payment link"
