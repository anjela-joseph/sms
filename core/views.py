import time
from smtplib import SMTPException
from datetime import date

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from accounts.permissions import internal_required

from . import billing
from .models import ZERO, Invoice, Payment, Plan, QuotationTemplate, Subscription, add_months

S, I = Subscription.Status, Invoice.Status
DUE = ExpressionWrapper(F("total") - F("amount_paid"), output_field=DecimalField(max_digits=14, decimal_places=2))


@login_required
def home(request):
    return redirect("dashboard" if request.user.is_internal else "portal")


# --------------------------------------------------------------------------- finance dashboard

@internal_required
def dashboard(request):
    t0 = time.perf_counter()
    today = timezone.localdate()
    plan_id = request.GET.get("plan") or None
    try:
        d_from = date.fromisoformat(request.GET["from"]) if request.GET.get("from") else add_months(today, -5).replace(day=1)
        d_to = date.fromisoformat(request.GET["to"]) if request.GET.get("to") else today
    except ValueError:
        d_from, d_to = add_months(today, -5).replace(day=1), today

    subs = Subscription.objects.all()
    invs = Invoice.objects.exclude(status=I.CANCELLED)
    pays = Payment.objects.filter(date__range=(d_from, d_to))
    if plan_id:
        subs, invs, pays = subs.filter(plan_id=plan_id), invs.filter(subscription__plan_id=plan_id), \
            pays.filter(invoice__subscription__plan_id=plan_id)

    sub_counts = dict(subs.values_list("status").annotate(n=Count("id")).order_by())
    money0 = Coalesce(Sum(DUE), ZERO, output_field=DecimalField())
    open_q = Q(status=I.CONFIRMED)
    kpi = invs.aggregate(
        outstanding=Coalesce(Sum(DUE, filter=open_q & Q(due_date__gte=today)), ZERO, output_field=DecimalField()),
        overdue=Coalesce(Sum(DUE, filter=Invoice.overdue_q()), ZERO, output_field=DecimalField()),
        overdue_n=Count("id", filter=Invoice.overdue_q()),
        invoiced=Coalesce(Sum("total", filter=Q(invoice_date__range=(d_from, d_to))), ZERO,
                          output_field=DecimalField()),
    )
    kpi["revenue"] = pays.aggregate(v=Coalesce(Sum("amount"), ZERO, output_field=DecimalField()))["v"]

    by_month = {r["m"].strftime("%b %Y"): r["v"] for r in
                pays.annotate(m=TruncMonth("date")).values("m").annotate(v=Sum("amount")).order_by("m")}
    billed = {r["m"].strftime("%b %Y"): r["v"] for r in
              invs.filter(invoice_date__range=(d_from, d_to)).annotate(m=TruncMonth("invoice_date"))
              .values("m").annotate(v=Sum("total")).order_by("m")}
    months, m = [], d_from.replace(day=1)
    while m <= d_to:
        months.append(m.strftime("%b %Y"))
        m = add_months(m, 1)
    peak = max([float(v) for v in list(by_month.values()) + list(billed.values())] or [1]) or 1
    chart = [{"label": x, "paid": by_month.get(x, 0), "billed": billed.get(x, 0),
              "paid_pct": round(float(by_month.get(x, 0)) / peak * 100),
              "billed_pct": round(float(billed.get(x, 0)) / peak * 100)} for x in months]
    status_rows = [(label, sub_counts.get(value, 0)) for value, label in S.choices]

    overdue = (invs.filter(Invoice.overdue_q()).select_related("customer").annotate(due=DUE)
               .order_by("due_date")[:10])
    return render(request, "core/dashboard.html", {
        "kpi": kpi, "sub_counts": sub_counts, "active": sub_counts.get(S.ACTIVE, 0),
        "total_subs": sum(sub_counts.values()), "chart": chart, "overdue": overdue,
        "plans": Plan.objects.order_by("name"), "plan_id": plan_id, "d_from": d_from, "d_to": d_to,
        "status_rows": status_rows, "due_now": subs.filter(status=S.ACTIVE, next_billing_date__lte=today).count(),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000),
    })


@internal_required
@require_POST
def run_billing_view(request):
    from django.db import transaction
    from .billing_jobs import launch_billing
    from .models import BillingRun

    run = BillingRun.objects.create()
    transaction.on_commit(lambda: launch_billing(run.pk))
    return redirect("billing_result", pk=run.pk)


# --------------------------------------------------------------------------- new subscription (sales)

class NewSubscriptionForm(forms.Form):
    customer = forms.ModelChoiceField(User.objects.filter(role=User.Role.CUSTOMER, is_active=True).order_by("email"))
    template = forms.ModelChoiceField(QuotationTemplate.objects.select_related("plan"))
    start_date = forms.DateField(initial=date.today, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        template, start = data.get("template"), data.get("start_date")
        if template and start and not template.plan.is_available(start):
            raise forms.ValidationError(f"The plan “{template.plan.name}” is not currently available.")
        return data


@internal_required
def new_subscription(request):
    form = NewSubscriptionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        sub = billing.create_from_template(d["template"], d["customer"], request.user, d["start_date"])
        messages.success(request, f"{sub} created as Draft from “{d['template']}”. Move it through "
                                  "Quotation → Confirmed → Active with the status buttons.")
        return redirect("subscription_detail", pk=sub.pk)
    return render(request, "core/new_subscription.html", {"form": form})


# --------------------------------------------------------------------------- invoice page

class PaymentForm(forms.Form):
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    method = forms.ChoiceField(choices=Payment.Method.choices)
    date = forms.DateField(initial=date.today, widget=forms.DateInput(attrs={"type": "date"}))
    note = forms.CharField(required=False)


@login_required
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("customer", "subscription__plan"), pk=pk)
    if not request.user.is_internal and inv.customer_id != request.user.id:
        raise PermissionDenied
    if not request.user.is_internal and inv.status == I.DRAFT:
        raise PermissionDenied
    pay_form = PaymentForm(request.POST or None, initial={"amount": inv.amount_due})
    if request.method == "POST":
        if not request.user.is_internal:
            raise PermissionDenied
        action = request.POST.get("action")
        try:
            if action == "confirm":
                billing.confirm_invoice(inv)
            elif action == "cancel":
                billing.cancel_invoice(inv)
            elif action == "send":
                billing.send_invoice(inv, request)
            elif action == "pay" and pay_form.is_valid():
                d = pay_form.cleaned_data
                billing.record_payment(inv, d["amount"], d["method"], d["date"], d["note"], request.user)
            else:
                raise ValidationError("Check the payment details.")
            messages.success(request, f"Invoice {inv.number}: {action} done.")
            return redirect("invoice_detail", pk=inv.pk)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        except (SMTPException, OSError):
            messages.error(request, "Email could not be sent. Check your SMTP settings and try again.")
    return render(request, "core/invoice_detail.html", {
        "inv": inv, "lines": inv.lines.all(), "payments": inv.payments.all(), "pay_form": pay_form,
    })


# --------------------------------------------------------------------------- customer portal

@login_required
def portal(request):
    user = request.user
    subs = (Subscription.objects.filter(customer=user).exclude(status=S.DRAFT)
            .select_related("plan").prefetch_related("lines__product", "lines__variant__product"))
    invoices = Paginator(Invoice.objects.filter(customer=user).exclude(status=I.DRAFT)
                         .select_related("subscription"), 20).get_page(request.GET.get("page"))
    due = Invoice.objects.filter(customer=user, status=I.CONFIRMED).aggregate(
        v=Coalesce(Sum(DUE), ZERO, output_field=DecimalField()))["v"]
    return render(request, "core/portal.html", {"subs": subs, "invoices": invoices, "due": due,
                                                "today": timezone.localdate()})


@login_required
@require_POST
def portal_action(request, pk, action):
    sub = get_object_or_404(Subscription, pk=pk, customer=request.user)  # customers only touch their own
    try:
        if action == "pause":
            billing.change_status(sub, S.PAUSED, by_customer=True)
        elif action == "resume":
            billing.change_status(sub, S.ACTIVE, by_customer=True)
        elif action == "close":
            billing.change_status(sub, S.CLOSED, by_customer=True)
        elif action == "renew":
            billing.renew(sub)
        else:
            raise ValidationError("Unknown action.")
        messages.success(request, f"{sub.number}: {action} done.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("portal")