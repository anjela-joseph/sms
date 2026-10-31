from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import ZERO, Subscription, SubscriptionLine, money


def monthly_recurring_revenue(subscriptions):
    """Current gross contracted MRR, excluding tax and discounts; independent of report dates."""
    today = timezone.localdate()
    active = subscriptions.filter(status=Subscription.Status.ACTIVE, start_date__lte=today).filter(
        Q(expiry_date__isnull=True) | Q(expiry_date__gt=today))
    # Aggregate fees and lines separately so plans with multiple lines count their fee once.
    fees = dict(active.order_by().values_list("plan__billing_period").annotate(total=Sum("plan__price")))
    line_amount = ExpressionWrapper(Coalesce(F("unit_price"), ZERO) * F("quantity"),
                                    output_field=DecimalField(max_digits=20, decimal_places=2))
    lines = dict(SubscriptionLine.objects.filter(subscription__in=active.order_by().values("pk"))
                 .order_by().values_list("subscription__plan__billing_period").annotate(total=Sum(line_amount)))
    factors = {"monthly": Decimal(1), "yearly": Decimal(1) / 12,
               "weekly": Decimal(52) / 12, "daily": Decimal(365) / 12}
    return money(sum(((fees.get(period, ZERO) + lines.get(period, ZERO)) * factor
                      for period, factor in factors.items()), ZERO))
