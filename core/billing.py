"""Billing engine: subscription lifecycle, invoice generation, discounts, payments.

All money rules live here so views, admin actions, the cron command and tests share one path.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import (ZERO, Discount, Invoice, InvoiceLine, Payment, Subscription,
                     SubscriptionLine, money)

log = logging.getLogger(__name__)
S = Subscription.Status
I = Invoice.Status


# --------------------------------------------------------------------------- subscriptions

def create_from_template(template, customer, salesperson=None, start_date=None):
    start = start_date or timezone.localdate()
    with transaction.atomic():
        sub = Subscription.objects.create(
            customer=customer, plan=template.plan, template=template, start_date=start,
            quotation_valid_until=start + timedelta(days=template.validity_days),
            salesperson=salesperson,
        )
        for tl in template.lines.select_related("product", "variant"):
            SubscriptionLine.objects.create(subscription=sub, product=tl.product, variant=tl.variant,
                                            quantity=tl.quantity, tax=tl.tax)
    return sub


def _expiry_for(sub):
    if sub.expiry_date or not sub.template:
        return sub.expiry_date
    d = sub.start_date
    for _ in range(sub.template.duration_periods):
        d = sub.plan.next_date(d)
    return d


def change_status(sub, new_status, by_customer=False):
    """Move a subscription through its state machine. Raises ValidationError on a bad move."""
    if new_status not in sub.allowed_transitions():
        raise ValidationError(f"Cannot move {sub} from {sub.get_status_display()} to {new_status}.")
    plan = sub.plan
    if new_status == S.PAUSED and not plan.pausable:
        raise ValidationError("This plan does not allow pausing.")
    if new_status == S.CLOSED and sub.status == S.ACTIVE and not plan.closable:
        raise ValidationError("This plan cannot be closed early.")
    if new_status == S.CONFIRMED:
        if sub.quotation_valid_until and sub.quotation_valid_until < timezone.localdate():
            raise ValidationError("The quotation has expired.")
        total_qty = sum(l.quantity for l in sub.lines.all())
        if total_qty < plan.min_quantity:
            raise ValidationError(f"Plan needs at least {plan.min_quantity} units.")
    if by_customer and new_status not in (S.PAUSED, S.ACTIVE, S.CLOSED):
        raise ValidationError("Not allowed from the portal.")

    if new_status == S.ACTIVE:
        sub.expiry_date = _expiry_for(sub)
        if sub.next_billing_date is None:
            sub.next_billing_date = sub.start_date
        elif sub.status == S.PAUSED:
            # Resume: don't back-bill the paused stretch.
            sub.next_billing_date = max(sub.next_billing_date, timezone.localdate())
    sub.status = new_status
    sub.save()
    return sub


def renew(sub):
    if not sub.plan.renewable:
        raise ValidationError("This plan is not renewable.")
    if sub.status not in (S.ACTIVE, S.CLOSED):
        raise ValidationError("Only active or closed subscriptions can be renewed.")
    base = sub.expiry_date or timezone.localdate()
    periods = sub.template.duration_periods if sub.template else 12
    new_expiry = base
    for _ in range(periods):
        new_expiry = sub.plan.next_date(new_expiry)
    sub.expiry_date = new_expiry
    if sub.status == S.CLOSED:
        sub.status = S.ACTIVE
        sub.next_billing_date = max(base, timezone.localdate())
    sub.save()
    return sub


# --------------------------------------------------------------------------- discounts

def _discount_applies(d, lines, untaxed, qty, on):
    if not d.is_valid_on(on):
        return False
    if untaxed < d.min_purchase or qty < d.min_quantity:
        return False
    product_ids = set(d.products.values_list("id", flat=True))
    if product_ids and not any(l["product_id"] in product_ids for l in lines):
        return False
    return True


def _discount_amounts(d, lines):
    """Return {line_index: discount_amount}. Scope = chosen products, or whole subscription."""
    product_ids = set(d.products.values_list("id", flat=True))
    eligible = [i for i, l in enumerate(lines) if not product_ids or l["product_id"] in product_ids]
    base = sum((lines[i]["subtotal"] for i in eligible), ZERO)
    if base <= 0:
        return {}
    if d.discount_type == Discount.Type.PERCENT:
        return {i: money(lines[i]["subtotal"] * d.value / 100) for i in eligible}
    # Fixed: spread across eligible lines by weight, never more than the base.
    total_off = min(d.value, base)
    out, given = {}, ZERO
    for n, i in enumerate(eligible):
        share = total_off - given if n == len(eligible) - 1 else money(total_off * lines[i]["subtotal"] / base)
        out[i] = share
        given += share
    return out


def pick_discount(sub, lines, on):
    """The subscription's own discount if it qualifies, else the best automatic one."""
    untaxed = sum((l["subtotal"] for l in lines), ZERO)
    qty = sum(int(l["quantity"]) for l in lines if l["product_id"])
    candidates = [sub.discount] if sub.discount_id else list(
        Discount.objects.filter(is_active=True, start_date__lte=on)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=on)).prefetch_related("products"))
    best, best_amt = None, ZERO
    for d in candidates:
        if d and _discount_applies(d, lines, untaxed, qty, on):
            amt = sum(_discount_amounts(d, lines).values(), ZERO)
            if amt > best_amt:
                best, best_amt = d, amt
    return best


# --------------------------------------------------------------------------- invoices

def build_invoice(sub, period_start, confirm=None, today=None):
    """Create the invoice for one billing period. Returns None if that period is already billed."""
    today = today or timezone.localdate()
    confirm = settings.AUTO_CONFIRM_INVOICES if confirm is None else confirm
    period_end = sub.plan.next_date(period_start) - timedelta(days=1)

    lines = []
    if sub.plan.price > 0:
        lines.append(dict(product_id=None, description=f"{sub.plan.name} – plan fee",
                          quantity=Decimal(1), unit_price=sub.plan.price, subtotal=money(sub.plan.price),
                          tax_rate=ZERO))
    for l in sub.lines.select_related("product", "variant__product", "tax"):
        lines.append(dict(product_id=l.product_id, description=l.description, quantity=Decimal(l.quantity),
                          unit_price=l.unit_price, subtotal=l.amount,
                          tax_rate=l.tax.rate if l.tax else ZERO))

    discount = pick_discount(sub, lines, today)
    off = _discount_amounts(discount, lines) if discount else {}

    try:
        with transaction.atomic():
            inv = Invoice.objects.create(
                subscription=sub, customer_id=sub.customer_id, invoice_date=today,
                due_date=today + timedelta(days=sub.payment_terms),
                period_start=period_start, period_end=period_end,
                status=I.CONFIRMED if confirm else I.DRAFT,
            )
            untaxed = disc_total = tax_total = ZERO
            objs = []
            for i, l in enumerate(lines):
                d = off.get(i, ZERO)
                net = l["subtotal"] - d
                tax = money(net * l["tax_rate"] / 100)
                objs.append(InvoiceLine(invoice=inv, product_id=l["product_id"], description=l["description"],
                                        quantity=l["quantity"], unit_price=l["unit_price"], discount=d,
                                        tax_rate=l["tax_rate"], tax_amount=tax, subtotal=net))
                untaxed += l["subtotal"]; disc_total += d; tax_total += tax
            InvoiceLine.objects.bulk_create(objs)
            inv.untaxed_amount, inv.discount_amount, inv.tax_amount = untaxed, disc_total, tax_total
            inv.total = untaxed - disc_total + tax_total
            inv.save(update_fields=["untaxed_amount", "discount_amount", "tax_amount", "total"])
            if discount and disc_total:
                Discount.objects.filter(pk=discount.pk).update(times_used=F("times_used") + 1)
    except IntegrityError:
        if Invoice.objects.filter(subscription=sub, period_start=period_start).exists():
            return None
        # Preserve the billing cursor on unexpected integrity errors.
        raise
    return inv


def bill_subscription(sub, today=None):
    """Bill every period that is due (catches up if billing didn't run for a while)."""
    today = today or timezone.localdate()
    created = []
    with transaction.atomic():
        sub = Subscription.objects.select_for_update(of=("self",)).select_related("plan", "discount").get(pk=sub.pk)
        if sub.status != S.ACTIVE:
            return created
        while sub.next_billing_date and sub.next_billing_date <= today:
            if sub.expiry_date and sub.next_billing_date >= sub.expiry_date:
                break
            inv = build_invoice(sub, sub.next_billing_date, today=today)
            if inv:
                created.append(inv)
            sub.next_billing_date = sub.plan.next_date(sub.next_billing_date)
        if sub.expiry_date and sub.expiry_date <= today and sub.plan.auto_close:
            sub.status = S.CLOSED
        sub.save(update_fields=["next_billing_date", "status"])
    return created


def run_billing(today=None):
    """Batch run: safe to call any number of times (cron + button)."""
    today = today or timezone.localdate()
    due = (Subscription.objects.filter(status=S.ACTIVE)
           .filter(Q(next_billing_date__lte=today) | Q(expiry_date__lte=today, plan__auto_close=True))
           .values_list("pk", flat=True))
    stats = {"subscriptions": 0, "invoices": 0, "errors": 0,
             "invoice_ids": [], "error_subscription_ids": []}
    for pk in due.iterator(chunk_size=500):
        try:
            made = bill_subscription(Subscription(pk=pk), today)
            stats["subscriptions"] += 1
            stats["invoices"] += len(made)
            stats["invoice_ids"].extend(inv.pk for inv in made)
        except Exception:  # one bad record must not stop the run, but log it
            log.exception("Billing failed for subscription %s", pk)
            stats["errors"] += 1
            stats["error_subscription_ids"].append(pk)
    return stats


def confirm_invoice(inv):
    if inv.status != I.DRAFT:
        raise ValidationError("Only draft invoices can be confirmed.")
    inv.status = I.CONFIRMED
    inv.save(update_fields=["status"])


def cancel_invoice(inv):
    if inv.status not in (I.DRAFT, I.CONFIRMED) or inv.amount_paid > 0:
        raise ValidationError("Only unpaid draft/confirmed invoices can be cancelled.")
    inv.status = I.CANCELLED
    inv.save(update_fields=["status"])


def send_invoice(inv, request=None):
    if inv.status == I.DRAFT:
        raise ValidationError("Confirm the invoice before sending it.")
    link = request.build_absolute_uri(f"/invoices/{inv.pk}/") if request else f"/invoices/{inv.pk}/"
    send_mail(f"Invoice {inv.number}",
              f"Hello,\n\nYour invoice {inv.number} for {settings.CURRENCY}{inv.total} is due on "
              f"{inv.due_date:%d %b %Y}.\nView it here: {link}\n\nBrightloop",
              settings.DEFAULT_FROM_EMAIL, [inv.customer.email])
    inv.sent_at = timezone.now()
    inv.save(update_fields=["sent_at"])


# --------------------------------------------------------------------------- payments

def record_payment(inv, amount, method, date=None, note="", user=None):
    amount = money(amount)
    with transaction.atomic():
        inv = Invoice.objects.select_for_update().get(pk=inv.pk)
        if inv.status != I.CONFIRMED:
            raise ValidationError("Payments can only be recorded on confirmed invoices.")
        if amount <= 0 or amount > inv.amount_due:
            raise ValidationError(f"Amount must be between 0 and {inv.amount_due}.")
        p = Payment.objects.create(invoice=inv, amount=amount, method=method,
                                   date=date or timezone.localdate(), note=note, recorded_by=user)
        inv.amount_paid += amount
        if inv.amount_paid >= inv.total:
            inv.status = I.PAID
        inv.save(update_fields=["amount_paid", "status"])
    return p
