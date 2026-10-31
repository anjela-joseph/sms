import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

ZERO = Decimal("0.00")


def money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# --------------------------------------------------------------------------- catalog

class Tax(models.Model):
    name = models.CharField(max_length=60)
    rate = models.DecimalField(max_digits=5, decimal_places=2, help_text="Percent, e.g. 18.00")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "taxes"

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


class Product(models.Model):
    class Type(models.TextChoices):
        SERVICE = "service", "Service / software"
        ADDON = "addon", "Add-on"
        GOODS = "goods", "Goods"

    name = models.CharField(max_length=150)
    product_type = models.CharField(max_length=20, choices=Type.choices, default=Type.SERVICE)
    sales_price = models.DecimalField(max_digits=12, decimal_places=2)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    default_tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    """e.g. attribute='Brand', value='Odoo', extra_price=560."""

    product = models.ForeignKey(Product, related_name="variants", on_delete=models.CASCADE)
    attribute = models.CharField(max_length=60)
    value = models.CharField(max_length=60)
    extra_price = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    class Meta:
        unique_together = ("product", "attribute", "value")

    @property
    def price(self):
        return self.product.sales_price + self.extra_price

    def __str__(self):
        return f"{self.product} ({self.attribute}: {self.value})"


class Plan(models.Model):
    class Period(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    name = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO,
                                help_text="Base recurring fee per period (added as its own invoice line if > 0).")
    billing_period = models.CharField(max_length=10, choices=Period.choices, default=Period.MONTHLY)
    min_quantity = models.PositiveIntegerField(default=1)
    start_date = models.DateField(default=date.today)
    end_date = models.DateField(null=True, blank=True)
    # Behaviour flags the admin sets per plan
    auto_close = models.BooleanField(default=True, help_text="Close subscriptions automatically at expiry.")
    closable = models.BooleanField(default=True, help_text="Can be closed early.")
    pausable = models.BooleanField(default=False)
    renewable = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.get_billing_period_display()})"

    def is_available(self, on=None):
        on = on or timezone.localdate()
        return self.start_date <= on and (self.end_date is None or on <= self.end_date)

    def next_date(self, d: date) -> date:
        return {
            self.Period.DAILY: lambda: d + timedelta(days=1),
            self.Period.WEEKLY: lambda: d + timedelta(weeks=1),
            self.Period.MONTHLY: lambda: add_months(d, 1),
            self.Period.YEARLY: lambda: add_months(d, 12),
        }[self.billing_period]()


# --------------------------------------------------------------------------- discounts

class Discount(models.Model):
    class Type(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENT = "percent", "Percentage"

    name = models.CharField(max_length=100)
    discount_type = models.CharField(max_length=10, choices=Type.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    min_purchase = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    min_quantity = models.PositiveIntegerField(default=0)
    start_date = models.DateField(default=date.today)
    end_date = models.DateField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = unlimited")
    times_used = models.PositiveIntegerField(default=0, editable=False)
    products = models.ManyToManyField(Product, blank=True,
                                      help_text="Leave empty to apply to the whole subscription.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        v = f"{self.value}%" if self.discount_type == self.Type.PERCENT else f"{settings.CURRENCY}{self.value}"
        return f"{self.name} (-{v})"

    def clean(self):
        if self.value is not None and self.value <= 0:
            raise ValidationError({"value": "Discount value must be positive."})
        if self.discount_type == self.Type.PERCENT and self.value is not None and self.value > 100:
            raise ValidationError({"value": "Percentage must be between 0 and 100."})
        if self.min_purchase is not None and self.min_purchase < 0:
            raise ValidationError({"min_purchase": "Minimum purchase cannot be negative."})
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date is before start date."})

    def is_valid_on(self, d: date) -> bool:
        if not self.is_active or d < self.start_date:
            return False
        if self.end_date and d > self.end_date:
            return False
        if self.usage_limit is not None and self.times_used >= self.usage_limit:
            return False
        return True


# --------------------------------------------------------------------------- quotation templates

class QuotationTemplate(models.Model):
    name = models.CharField(max_length=120)
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    validity_days = models.PositiveIntegerField(default=30)
    duration_periods = models.PositiveIntegerField(default=12, help_text="Subscription length in billing periods.")
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name


class TemplateLine(models.Model):
    template = models.ForeignKey(QuotationTemplate, related_name="lines", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.SET_NULL)


# --------------------------------------------------------------------------- subscriptions

class Subscription(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUOTATION = "quotation", "Quotation"
        CONFIRMED = "confirmed", "Confirmed"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        CLOSED = "closed", "Closed"

    TRANSITIONS = {
        Status.DRAFT: [Status.QUOTATION, Status.CLOSED],
        Status.QUOTATION: [Status.CONFIRMED, Status.DRAFT, Status.CLOSED],
        Status.CONFIRMED: [Status.ACTIVE, Status.CLOSED],
        Status.ACTIVE: [Status.PAUSED, Status.CLOSED],
        Status.PAUSED: [Status.ACTIVE, Status.CLOSED],
        Status.CLOSED: [],
    }

    class Terms(models.IntegerChoices):
        IMMEDIATE = 0, "Immediate"
        NET_15 = 15, "15 days"
        NET_30 = 30, "30 days"
        NET_45 = 45, "45 days"

    number = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="subscriptions",
                                 on_delete=models.PROTECT, limit_choices_to={"role": "customer"})
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    template = models.ForeignKey(QuotationTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    discount = models.ForeignKey(Discount, null=True, blank=True, on_delete=models.SET_NULL)
    start_date = models.DateField(default=date.today)
    expiry_date = models.DateField(null=True, blank=True)
    quotation_valid_until = models.DateField(null=True, blank=True)
    payment_terms = models.IntegerField(choices=Terms.choices, default=Terms.NET_30)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    next_billing_date = models.DateField(null=True, blank=True)
    salesperson = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    related_name="sold_subscriptions", on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["status", "next_billing_date"]),
            models.Index(fields=["status", "expiry_date"]),
        ]

    def __str__(self):
        return self.number or f"Subscription #{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"SUB{self.pk:06d}"
            super().save(update_fields=["number"])

    @property
    def untaxed_total(self):
        return sum((l.amount for l in self.lines.all()), ZERO) + self.plan.price

    def allowed_transitions(self):
        return self.TRANSITIONS.get(self.status, [])


class SubscriptionLine(models.Model):
    subscription = models.ForeignKey(Subscription, related_name="lines", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.SET_NULL)

    def save(self, *args, **kwargs):
        if self.unit_price is None:  # auto-fill from product + variant
            self.unit_price = self.product.sales_price + (self.variant.extra_price if self.variant else ZERO)
        if self.tax_id is None and self.product.default_tax_id:
            self.tax_id = self.product.default_tax_id
        super().save(*args, **kwargs)

    @property
    def amount(self):
        return money((self.unit_price or ZERO) * self.quantity)

    @property
    def description(self):
        return str(self.variant) if self.variant else self.product.name


# --------------------------------------------------------------------------- invoices & payments

class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=24, unique=True, null=True, blank=True, editable=False)
    subscription = models.ForeignKey(Subscription, null=True, blank=True, related_name="invoices",
                                     on_delete=models.PROTECT)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="invoices", on_delete=models.PROTECT)
    invoice_date = models.DateField(default=date.today)
    due_date = models.DateField()
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    untaxed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-id"]
        constraints = [
            # A cancelled invoice still reserves its subscription period.
            models.UniqueConstraint(
                fields=["subscription", "period_start"],
                name="uniq_invoice_per_subscription_period",
            )
        ]
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["invoice_date"]),
        ]

    def __str__(self):
        return self.number or f"Invoice #{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.number:
            self.number = f"INV/{self.invoice_date.year}/{self.pk:06d}"
            super().save(update_fields=["number"])

    @property
    def amount_due(self):
        return self.total - self.amount_paid

    @property
    def is_overdue(self):
        return self.status == self.Status.CONFIRMED and self.due_date < timezone.localdate()

    @classmethod
    def overdue_q(cls):
        return Q(status=cls.Status.CONFIRMED, due_date__lt=timezone.localdate())


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, related_name="lines", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, help_text="After discount, before tax")

    @property
    def total(self):
        return self.subtotal + self.tax_amount


class Payment(models.Model):
    class Method(models.TextChoices):
        BANK = "bank", "Bank transfer"
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"

    invoice = models.ForeignKey(Invoice, related_name="payments", on_delete=models.PROTECT)
    method = models.CharField(max_length=10, choices=Method.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField(default=date.today, db_index=True)
    note = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.get_method_display()} {self.amount} → {self.invoice}"


class BillingRun(models.Model):
    """Durable results for the background billing command and its paginated report."""
    status = models.CharField(max_length=12, default="queued", choices=[
        ("queued", "Queued"), ("running", "Running"), ("completed", "Completed"), ("failed", "Failed")])
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    subscriptions = models.PositiveIntegerField(default=0)
    invoice_ids = models.JSONField(default=list)
    error_subscription_ids = models.JSONField(default=list)
    error_message = models.CharField(max_length=255, blank=True)
    elapsed_seconds = models.FloatField(default=0)
