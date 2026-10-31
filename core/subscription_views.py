"""Internal subscription pages; lifecycle rules remain in billing."""
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, render

from accounts.permissions import internal_required
from .models import ZERO, Plan, Subscription, SubscriptionLine, money


@internal_required
def subscription_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    plan_id = request.GET.get("plan", "")
    subscriptions = Subscription.objects.select_related("customer", "plan")
    if query:
        subscriptions = subscriptions.filter(
            Q(number__icontains=query) | Q(customer__email__icontains=query)
            | Q(customer__company__icontains=query) | Q(customer__first_name__icontains=query)
            | Q(customer__last_name__icontains=query))
    if status:
        subscriptions = subscriptions.filter(status=status)
    if plan_id:
        try:
            plan_pk = int(plan_id)
            if not 0 < plan_pk <= 9223372036854775807:
                raise ValueError
            subscriptions = subscriptions.filter(plan_id=plan_pk)
        except ValueError:
            subscriptions = subscriptions.none()
    filters = request.GET.copy()
    filters.pop("page", None)
    return render(request, "core/subscription_list.html", {
        "page_obj": Paginator(subscriptions, 50).get_page(request.GET.get("page")),
        "q": query, "status": status, "plan_id": plan_id,
        "statuses": Subscription.Status.choices, "plans": Plan.objects.order_by("name"),
        "filter_query": filters.urlencode(),
    })


@internal_required
def subscription_detail(request, pk):
    sub = get_object_or_404(
        Subscription.objects.select_related("customer", "plan", "template", "salesperson")
        .prefetch_related(Prefetch("lines", queryset=SubscriptionLine.objects.select_related(
            "product", "variant__product", "tax").order_by("pk"))), pk=pk)
    lines = list(sub.lines.all())
    subtotal = sub.untaxed_total
    tax_total = sum((money(line.amount * line.tax.rate / 100) if line.tax else ZERO
                     for line in lines), ZERO)
    return render(request, "core/subscription_detail.html", {
        "sub": sub, "lines": lines, "subtotal": subtotal, "tax_total": tax_total,
        "total": subtotal + tax_total, "currency": settings.CURRENCY,
    })
