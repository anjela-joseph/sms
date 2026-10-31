"""Billing and report pages, kept separate from subscription and account views."""
from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, Value
from django.db.models.functions import Concat
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import admin_required, internal_required
from .models import ZERO, BillingRun, Discount, Invoice, Payment


class DateRangeForm(forms.Form):
    date_from = forms.DateField(required=False, label="From", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, label="To", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        start, end = data.get("date_from"), data.get("date_to")
        if start and end and start > end:
            raise forms.ValidationError("From date must be on or before To date.")
        return data


class InvoiceFilterForm(DateRangeForm):
    q = forms.CharField(required=False, max_length=150, label="Invoice number or customer")
    status = forms.ChoiceField(required=False, choices=[
        ("", "All statuses"), ("paid", "Paid"), ("outstanding", "Outstanding"),
        ("overdue", "Overdue"), ("draft", "Draft"), ("cancelled", "Cancelled"),
    ])


def filtered_invoices(form):
    invoices = Invoice.objects.select_related("customer", "subscription__plan").order_by("-invoice_date", "-pk")
    if not form.is_valid():
        return invoices.none()
    data = form.cleaned_data
    if data["q"]:
        term = data["q"]
        invoices = invoices.alias(customer_name=Concat("customer__first_name", Value(" "), "customer__last_name")).filter(
            Q(number__icontains=term) | Q(customer__email__icontains=term)
            | Q(customer__first_name__icontains=term) | Q(customer__last_name__icontains=term)
            | Q(customer__company__icontains=term) | Q(customer_name__icontains=term))
    if data["status"] == "overdue":
        invoices = invoices.filter(Invoice.overdue_q())
    elif data["status"] == "outstanding":
        invoices = invoices.filter(status=Invoice.Status.CONFIRMED, due_date__gte=timezone.localdate())
    elif data["status"]:
        invoices = invoices.filter(status=data["status"])
    if data["date_from"]:
        invoices = invoices.filter(invoice_date__gte=data["date_from"])
    if data["date_to"]:
        invoices = invoices.filter(invoice_date__lte=data["date_to"])
    return invoices


def page_context(request, queryset):
    params = request.GET.copy()
    params.pop("page", None)
    return {"page_obj": Paginator(queryset, 50).get_page(request.GET.get("page")),
            "filter_query": params.urlencode()}


@internal_required
def invoice_list(request):
    form = InvoiceFilterForm(request.GET)
    context = page_context(request, filtered_invoices(form))
    return render(request, "core/invoice_list.html", {**context, "form": form})








@internal_required
def billing_result(request, pk):
    run = get_object_or_404(BillingRun, pk=pk)
    invoices = Invoice.objects.filter(pk__in=run.invoice_ids).select_related("subscription", "customer")
    return render(request, "core/billing_result.html", {
        **page_context(request, invoices), "run": run,
        "created_count": len(run.invoice_ids), "error_count": len(run.error_subscription_ids),
    })


class PaymentFilterForm(DateRangeForm):
    method = forms.ChoiceField(required=False, choices=[("", "All methods"), *Payment.Method.choices])


@internal_required
def payment_list(request):
    form = PaymentFilterForm(request.GET)
    payments = Payment.objects.select_related("invoice__customer", "recorded_by").order_by("-date", "-pk")
    if form.is_valid():
        data = form.cleaned_data
        if data["method"]:
            payments = payments.filter(method=data["method"])
        if data["date_from"]:
            payments = payments.filter(date__gte=data["date_from"])
        if data["date_to"]:
            payments = payments.filter(date__lte=data["date_to"])
    else:
        payments = payments.none()
    totals = dict(payments.order_by().values_list("method").annotate(total=Sum("amount")))
    return render(request, "core/payment_list.html", {
        **page_context(request, payments), "form": form, "total": sum(totals.values(), ZERO),
        "method_totals": [(label, totals.get(value, ZERO)) for value, label in Payment.Method.choices],
    })


class DiscountForm(forms.ModelForm):
    class Meta:
        model = Discount
        fields = ["name", "discount_type", "value", "min_purchase", "min_quantity", "start_date", "end_date",
                  "usage_limit", "products", "is_active"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}),
                   "end_date": forms.DateInput(attrs={"type": "date"})}


@internal_required
def discount_list(request):
    discounts = Discount.objects.order_by("-pk").prefetch_related("products")
    context = page_context(request, discounts)
    for discount in context["page_obj"]:
        discount.available_today = discount.is_valid_on(timezone.localdate())
    return render(request, "core/discount_list.html", context)


@admin_required
@transaction.atomic
def discount_edit(request, pk=None):
    discount = get_object_or_404(Discount.objects.select_for_update(), pk=pk) if pk else None
    form = DiscountForm(request.POST if request.method == "POST" else None, instance=discount)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Discount saved. It will be considered automatically for future invoices.")
        return redirect("discount_list")
    return render(request, "core/discount_form.html", {"form": form, "discount": discount})
