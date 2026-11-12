from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice, PaymentLink, Transaction
from app.services.common import aware, as_uuid
from app.services.invoices import InvoiceService

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _pct(cur: float, prev: float) -> float | None:
    if prev == 0:
        return None if cur == 0 else 100.0
    return round((cur - prev) / prev * 100, 1)


PAID = ("completed", "partially_refunded")  # payments that settled and are not fully refunded


def _net(t: Transaction) -> float:
    """What the merchant kept: the payment minus completed refunds."""
    return float(t.amount) - float(t.refunded_amount or 0)


def _rate(txns: list[Transaction]) -> float | None:
    done = sum(1 for t in txns if t.status in PAID)
    failed = sum(1 for t in txns if t.status == "failed")
    return round(done / (done + failed) * 100, 1) if done + failed else None


class AnalyticsService:
    def __init__(self, db: Session, user_id, now: datetime | None = None):
        self.db, self.user_id = db, as_uuid(user_id)
        self.now = now or datetime.now(timezone.utc)

    def _txns(self, start: datetime, end: datetime, currency: str | None = None) -> list[Transaction]:
        q = select(Transaction).where(Transaction.user_id == self.user_id, Transaction.created_at >= start, Transaction.created_at < end)
        if currency:
            q = q.where(Transaction.currency == currency)
        return list(self.db.scalars(q).unique().all())

    def currencies(self) -> list[str]:
        return sorted({c for c in self.db.scalars(select(Transaction.currency).where(Transaction.user_id == self.user_id)).all()})

    def pick_currency(self, requested: str | None) -> str:
        if requested:
            return requested.upper()
        cs = self.currencies()
        return "USD" if not cs or "USD" in cs else cs[0]

    def series(self, txns: list[Transaction], start: datetime, days: int) -> list[float]:
        monthly = days > 90
        n = 12 if monthly else days
        buckets = [0.0] * n
        for t in txns:
            if t.status not in PAID:
                continue
            d = aware(t.created_at)
            idx = ((d.year - start.year) * 12 + d.month - start.month) if monthly else (d - start).days
            if 0 <= idx < n:
                buckets[idx] += _net(t)
        return [round(b, 2) for b in buckets]

    def overview(self, days: int = 30, currency: str | None = None) -> dict:
        currency = self.pick_currency(currency)
        InvoiceService(self.db, self.user_id).mark_overdue()
        start = self.now - timedelta(days=days)
        prev_start = start - timedelta(days=days)
        cur = self._txns(start, self.now + timedelta(seconds=1), currency)
        prev = self._txns(prev_start, start, currency)
        revenue = sum((_net(t) for t in cur if t.status in PAID), 0.0)
        prev_revenue = sum((_net(t) for t in prev if t.status in PAID), 0.0)
        today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        processed_today = sum(1 for t in cur if aware(t.created_at) >= today)

        open_invoices = list(self.db.scalars(select(Invoice).where(
            Invoice.user_id == self.user_id, Invoice.currency == currency, Invoice.status.in_(["pending", "overdue"]))).unique().all())
        pending_total = sum((float(i.total_amount) for i in open_invoices), 0.0)
        aging = [(date.today() - (i.due_date or aware(i.created_at).date())).days for i in open_invoices]
        overdue = [i for i in open_invoices if i.status == "overdue"]

        rate, prev_rate = _rate(cur), _rate(prev)
        total = len(cur)
        series_cur = self.series(cur, start, days)
        series_prev = self.series(prev, prev_start, days)
        recent = list(self.db.scalars(select(Transaction).where(Transaction.user_id == self.user_id)
                                      .order_by(Transaction.created_at.desc()).limit(5)).unique().all())
        return {
            "currency": currency, "currencies": self.currencies() or [currency], "days": days,
            "revenue": {"total": round(revenue, 2), "previous": round(prev_revenue, 2), "change_pct": _pct(revenue, prev_revenue),
                        "series": series_cur, "previous_series": series_prev, "granularity": "month" if days > 90 else "day", "start": start.date().isoformat(),
                        "daily_average": round(revenue / days, 2)},
            "transactions": {"total": total, "previous": len(prev), "change_pct": _pct(total, len(prev)), "processed_today": processed_today},
            "pending": {"total": round(pending_total, 2), "count": len(open_invoices), "overdue_count": len(overdue),
                        "overdue_total": round(sum(float(i.total_amount) for i in overdue), 2), "earliest_aging_days": max(aging) if aging else None},
            "success_rate": {"value": rate, "previous": prev_rate,
                             "change_pct": None if rate is None or prev_rate is None else round(rate - prev_rate, 1),
                             "failed_pct": round(sum(1 for t in cur if t.status == "failed") / total * 100, 1) if total else 0,
                             "refunded_pct": round(sum(1 for t in cur if t.status in ("refunded", "partially_refunded")) / total * 100, 1) if total else 0},
            "recent_transactions": recent,
            "insights": self.insights(cur, prev, revenue, prev_revenue, overdue, currency),
        }

    # -------------------------------------------------------------- insights (deterministic, computed from data)
    def insights(self, cur, prev, revenue, prev_revenue, overdue, currency) -> list[dict]:
        out: list[dict] = []
        change = _pct(revenue, prev_revenue)
        if change is not None:
            out.append({"kind": "momentum", "title": "Revenue Momentum", "tone": "positive" if change >= 0 else "negative",
                        "text": f"Revenue {'increased' if change >= 0 else 'decreased'} by {abs(change)}% versus the previous period ({currency} {revenue:,.2f} vs {prev_revenue:,.2f})."})
        elif revenue == 0:
            out.append({"kind": "momentum", "title": "No revenue yet", "tone": "neutral", "text": "No completed payments in this period. Create an invoice or payment link to get started."})
        if overdue:
            total = sum(float(i.total_amount) for i in overdue)
            out.append({"kind": "action", "title": "Action Recommended", "tone": "warning", "action": "Review & prepare reminders",
                        "text": f"{len(overdue)} overdue invoice(s) totalling {currency} {total:,.2f} need attention."})
        by_day: dict[int, float] = defaultdict(float)
        for t in cur:
            if t.status in PAID:
                by_day[aware(t.created_at).weekday()] += _net(t)
        if by_day and revenue:
            d, v = max(by_day.items(), key=lambda kv: kv[1])
            out.append({"kind": "pattern", "title": "Temporal Pattern", "tone": "neutral",
                        "text": f"{WEEKDAYS[d]} is your strongest day: {v / revenue * 100:.0f}% of revenue in this period."})
        failed = [t for t in cur if t.status == "failed"]
        if failed:
            out.append({"kind": "risk", "title": "Failed Payments", "tone": "warning", "text": f"{len(failed)} payment(s) failed in this period. Follow up with those customers."})
        return out

    # -------------------------------------------------------------- analytics page
    def analytics(self, days: int = 30, currency: str | None = None) -> dict:
        ov = self.overview(days, currency)
        start = self.now - timedelta(days=days)
        cur = self._txns(start, self.now + timedelta(seconds=1), ov["currency"])
        done = [t for t in cur if t.status in PAID]
        by_customer: dict[str, dict] = {}
        for t in done:
            name = t.customer.name if t.customer else "Unassigned"
            row = by_customer.setdefault(name, {"customer": name, "revenue": 0.0, "payments": 0})
            row["revenue"] = round(row["revenue"] + _net(t), 2)
            row["payments"] += 1
        by_provider: dict[str, float] = defaultdict(float)
        by_status: dict[str, int] = defaultdict(int)
        for t in cur:
            by_status[t.status] += 1
            if t.status in PAID:
                by_provider[t.provider] += _net(t)
        avg = round(sum(_net(t) for t in done) / len(done), 2) if done else 0.0
        return {
            "currency": ov["currency"], "days": days, "revenue": ov["revenue"], "success_rate": ov["success_rate"],
            "volume": {"total": len(cur), "by_status": dict(by_status), "series": self.volume_series(cur, start, days)},
            "average_transaction_value": avg,
            "top_customers": sorted(by_customer.values(), key=lambda r: -r["revenue"])[:8],
            "revenue_by_provider": {k: round(v, 2) for k, v in by_provider.items()},
            "insights": ov["insights"],
        }

    def volume_series(self, txns, start, days) -> list[int]:
        monthly = days > 90
        n = 12 if monthly else days
        b = [0] * n
        for t in txns:
            d = aware(t.created_at)
            idx = ((d.year - start.year) * 12 + d.month - start.month) if monthly else (d - start).days
            if 0 <= idx < n:
                b[idx] += 1
        return b


def money_sum(rows, attr) -> float:
    return round(sum(float(getattr(r, attr)) for r in rows), 2)


def invoice_summary(db: Session, user_id, currency: str | None = None) -> dict:
    uid = as_uuid(user_id)
    InvoiceService(db, uid).mark_overdue()
    cur = AnalyticsService(db, uid).pick_currency(currency)
    allrows = list(db.scalars(select(Invoice).where(Invoice.user_id == uid)).unique().all())
    rows = [i for i in allrows if i.currency == cur]
    by = lambda st: [i for i in rows if i.status in st]
    return {
        "currency": cur,
        "counts": {"all": len(allrows), **{k: sum(1 for i in allrows if i.status == k) for k in ("paid", "pending", "overdue", "draft", "cancelled")}},
        "total_invoiced": money_sum([i for i in rows if i.status != "cancelled"], "total_amount"),
        "paid": {"total": money_sum(by({"paid"}), "total_amount"), "count": len(by({"paid"}))},
        "pending": {"total": money_sum(by({"pending"}), "total_amount"), "count": len(by({"pending"}))},
        "overdue": {"total": money_sum(by({"overdue"}), "total_amount"), "count": len(by({"overdue"}))},
    }


def transaction_summary(db: Session, user_id, currency: str | None = None) -> dict:
    uid = as_uuid(user_id)
    cur = AnalyticsService(db, uid).pick_currency(currency)
    rows = list(db.scalars(select(Transaction).where(Transaction.user_id == uid, Transaction.currency == cur)).unique().all())
    pick = lambda st: [t for t in rows if t.status in st]
    done, failed = pick(set(PAID)), pick({"failed"})
    refunded = pick({"refunded", "partially_refunded"})
    return {
        "currency": cur, "total": {"volume": money_sum(rows, "amount"), "count": len(rows)},
        "settled": {"total": round(sum(_net(t) for t in done), 2), "count": len(done),
                    "rate": round(len(done) / (len(done) + len(failed)) * 100, 1) if done or failed else None},
        "pending": {"total": money_sum(pick({"created", "pending"}), "amount"), "count": len(pick({"created", "pending"}))},
        "failed": {"total": money_sum(failed, "amount"), "count": len(failed)},
        "refunded": {"total": money_sum(refunded, "refunded_amount"), "count": len(refunded)},
    }


def link_summary(db: Session, user_id, currency: str | None = None) -> dict:
    uid = as_uuid(user_id)
    cur = AnalyticsService(db, uid).pick_currency(currency)
    links = list(db.scalars(select(PaymentLink).where(PaymentLink.user_id == uid)).all())
    paid = [l for l in links if l.status == "paid"]
    return {"currency": cur, "total": len(links), "active": sum(1 for l in links if l.status == "active"), "paid": len(paid),
            "paid_volume": round(sum(float(l.amount) for l in paid if l.currency == cur), 2),
            "conversion_rate": round(len(paid) / len(links) * 100, 1) if links else None}
