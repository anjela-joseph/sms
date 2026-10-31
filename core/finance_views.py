"""Billing and report pages, separate from account/subscription views."""
from django import forms
from django.core.paginator import Paginator
from django.db.models import Q, Value
from django.db.models.functions import Concat
from django.shortcuts import render
from django.utils import timezone
from accounts.permissions import internal_required
from .models import Invoice


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
