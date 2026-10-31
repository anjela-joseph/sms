"""Back office. Django admin covers CRUD for every entity; business actions call core.billing."""
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html

from . import billing
from .models import (Discount, Invoice, InvoiceLine, Payment, Plan, Product, ProductVariant,
                     QuotationTemplate, Subscription, SubscriptionLine, Tax, TemplateLine)

admin.site.site_header = "Brightloop back office"
admin.site.site_title = "Brightloop"
admin.site.index_title = "Subscriptions & billing"


class AdminOnlyMixin:
    """Admin can do everything; staff can view. Used for config the brief reserves for admin."""

    def has_add_permission(self, request, *a):
        return request.user.is_admin

    def has_change_permission(self, request, obj=None):
        return request.user.is_admin

    def has_delete_permission(self, request, obj=None):
        return request.user.is_admin

    def has_view_permission(self, request, obj=None):
        return request.user.is_internal


class InternalMixin:
    """Admin and staff manage day-to-day records."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_internal

    def has_view_permission(self, request, obj=None):
        return request.user.is_internal

    has_add_permission = has_change_permission = lambda self, request, *a, **k: request.user.is_internal

    def has_delete_permission(self, request, obj=None):
        return request.user.is_admin


def run_action(modeladmin, request, queryset, fn, ok):
    done = 0
    for obj in queryset:
        try:
            fn(obj)
            done += 1
        except ValidationError as e:
            modeladmin.message_user(request, f"{obj}: {'; '.join(e.messages)}", messages.ERROR)
    if done:
        modeladmin.message_user(request, f"{done} {ok}.", messages.SUCCESS)


# --------------------------------------------------------------------------- catalog

@admin.register(Tax)
class TaxAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "rate", "is_active")


class VariantInline(AdminOnlyMixin, admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.register(Product)
class ProductAdmin(AdminOnlyMixin, InternalMixin, admin.ModelAdmin):
    list_display = ("name", "product_type", "sales_price", "cost_price", "default_tax", "is_active")
    list_filter = ("product_type", "is_active")
    search_fields = ("name",)
    inlines = [VariantInline]


@admin.register(Plan)
class PlanAdmin(AdminOnlyMixin, InternalMixin, admin.ModelAdmin):
    list_display = ("name", "price", "billing_period", "min_quantity", "start_date", "end_date",
                    "auto_close", "closable", "pausable", "renewable")
    list_filter = ("billing_period", "pausable", "renewable")


@admin.register(Discount)
class DiscountAdmin(AdminOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "discount_type", "value", "min_purchase", "min_quantity",
                    "start_date", "end_date", "usage", "is_active")
    list_filter = ("discount_type", "is_active")
    filter_horizontal = ("products",)
    readonly_fields = ("times_used",)

    @admin.display(description="Used")
    def usage(self, d):
        return f"{d.times_used} / {d.usage_limit or '∞'}"


class TemplateLineInline(admin.TabularInline):
    model = TemplateLine
    extra = 1
    autocomplete_fields = ("product",)


@admin.register(QuotationTemplate)
class QuotationTemplateAdmin(InternalMixin, admin.ModelAdmin):
    list_display = ("name", "plan", "validity_days", "duration_periods")
    inlines = [TemplateLineInline]


# --------------------------------------------------------------------------- subscriptions

class SubscriptionLineInline(admin.TabularInline):
    model = SubscriptionLine
    extra = 1
    autocomplete_fields = ("product",)
    fields = ("product", "variant", "quantity", "unit_price", "tax", "line_amount")
    readonly_fields = ("line_amount",)

    @admin.display(description="Amount")
    def line_amount(self, l):
        return l.amount if l.pk else "-"


def _transition(target, label):
    def action(modeladmin, request, queryset):
        run_action(modeladmin, request, queryset, lambda s: billing.change_status(s, target), label)
    action.__name__ = f"mark_{target}"
    action.short_description = f"Move to {target.title()}"
    return action


@admin.register(Subscription)
class SubscriptionAdmin(InternalMixin, admin.ModelAdmin):
    list_display = ("number", "customer", "plan", "start_date", "expiry_date", "next_billing_date",
                    "status_badge", "payment_terms")
    list_filter = ("status", "plan", "payment_terms")
    search_fields = ("number", "customer__email", "customer__company")
    list_select_related = ("customer", "plan")
    autocomplete_fields = ("customer",)
    readonly_fields = ("number", "next_billing_date", "created_at")
    inlines = [SubscriptionLineInline]
    list_per_page = 50
    show_full_result_count = False  # avoids a COUNT(*) on big tables
    actions = [_transition("quotation", "sent as quotation"), _transition("confirmed", "confirmed"),
               _transition("active", "activated"), _transition("paused", "paused"),
               _transition("closed", "closed"), "generate_invoice_now", "renew_selected"]

    def save_model(self, request, obj, form, change):
        if not obj.salesperson_id:
            obj.salesperson = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, s):
        colors = {"draft": "#888", "quotation": "#b58900", "confirmed": "#268bd2",
                  "active": "#2e7d32", "paused": "#cb4b16", "closed": "#555"}
        return format_html('<b style="color:{}">{}</b>', colors[s.status], s.get_status_display())

    @admin.action(description="Generate invoice(s) now for due periods")
    def generate_invoice_now(self, request, queryset):
        n = sum(len(billing.bill_subscription(s)) for s in queryset)
        self.message_user(request, f"{n} invoice(s) created.", messages.SUCCESS)

    @admin.action(description="Renew")
    def renew_selected(self, request, queryset):
        run_action(self, request, queryset, billing.renew, "renewed")


# --------------------------------------------------------------------------- invoices

class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False
    readonly_fields = ("description", "quantity", "unit_price", "discount", "subtotal", "tax_rate", "tax_amount")
    fields = readonly_fields

    def has_add_permission(self, *a):
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ("date", "method", "amount", "note", "recorded_by")

    def has_add_permission(self, *a):
        return False


class OverdueFilter(admin.SimpleListFilter):
    title = "payment state"
    parameter_name = "due"

    def lookups(self, request, model_admin):
        return [("overdue", "Overdue"), ("outstanding", "Outstanding (not yet due)")]

    def queryset(self, request, qs):
        today = timezone.localdate()
        if self.value() == "overdue":
            return qs.filter(Invoice.overdue_q())
        if self.value() == "outstanding":
            return qs.filter(status=Invoice.Status.CONFIRMED, due_date__gte=today)
        return qs


@admin.register(Invoice)
class InvoiceAdmin(InternalMixin, admin.ModelAdmin):
    list_display = ("number", "customer", "subscription", "invoice_date", "due_date", "total",
                    "amount_paid", "status_badge", "open_link")
    list_filter = ("status", OverdueFilter, "invoice_date")
    search_fields = ("number", "customer__email", "subscription__number")
    list_select_related = ("customer", "subscription")
    date_hierarchy = "invoice_date"
    readonly_fields = ("number", "subscription", "customer", "period_start", "period_end", "untaxed_amount",
                       "discount_amount", "tax_amount", "total", "amount_paid", "sent_at", "status")
    fields = ("number", "status", "customer", "subscription", "invoice_date", "due_date",
              ("period_start", "period_end"), ("untaxed_amount", "discount_amount", "tax_amount", "total"),
              "amount_paid", "sent_at")
    inlines = [InvoiceLineInline, PaymentInline]
    list_per_page = 50
    show_full_result_count = False
    actions = ["confirm", "cancel", "send"]

    def has_add_permission(self, request):
        return False  # invoices come from subscriptions / billing run

    @admin.display(description="Status", ordering="status")
    def status_badge(self, inv):
        if inv.is_overdue:
            return format_html('<b style="color:{}">{}</b>', "#c62828", "Overdue")
        colors = {"draft": "#888", "confirmed": "#268bd2", "paid": "#2e7d32", "cancelled": "#555"}
        return format_html('<b style="color:{}">{}</b>', colors[inv.status], inv.get_status_display())

    @admin.display(description="")
    def open_link(self, inv):
        return format_html('<a href="/invoices/{}/">View / print / pay</a>', inv.pk)

    @admin.action(description="Confirm")
    def confirm(self, request, qs):
        run_action(self, request, qs, billing.confirm_invoice, "confirmed")

    @admin.action(description="Cancel")
    def cancel(self, request, qs):
        run_action(self, request, qs, billing.cancel_invoice, "cancelled")

    @admin.action(description="Send by email")
    def send(self, request, qs):
        run_action(self, request, qs, lambda i: billing.send_invoice(i, request), "sent")


@admin.register(Payment)
class PaymentAdmin(InternalMixin, admin.ModelAdmin):
    list_display = ("date", "invoice", "method", "amount", "note", "recorded_by")
    list_filter = ("method", "date")
    search_fields = ("invoice__number", "invoice__customer__email")
    list_select_related = ("invoice", "recorded_by")
    autocomplete_fields = ("invoice",)
    fields = ("invoice", "method", "amount", "date", "note")

    def has_change_permission(self, request, obj=None):
        return obj is None and request.user.is_internal  # payments are immutable once recorded

    def save_model(self, request, obj, form, change):
        # Route through the billing engine so the invoice balance/status update together.
        p = billing.record_payment(obj.invoice, obj.amount, obj.method, obj.date, obj.note, request.user)
        obj.pk = p.pk

    def get_form(self, request, obj=None, **kw):
        form = super().get_form(request, obj, **kw)
        base_clean = form.clean

        def clean(f):
            data = base_clean(f)
            inv, amt = data.get("invoice"), data.get("amount")
            if inv and amt is not None:
                if inv.status != Invoice.Status.CONFIRMED:
                    raise ValidationError("Payments can only be recorded on confirmed invoices.")
                if amt <= 0 or amt > inv.amount_due:
                    raise ValidationError(f"Amount must be between 0 and {inv.amount_due}.")
            return data
        form.clean = clean
        return form
