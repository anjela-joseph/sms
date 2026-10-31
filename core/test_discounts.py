from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal as D
from threading import Barrier

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from accounts.models import User
from . import billing
from .models import Discount, Invoice, Plan, Product, Subscription, SubscriptionLine


class DiscountRules(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.customer = User.objects.create_user("discount@customer.test")
        cls.plan = Plan.objects.create(name="Monthly", price=100)
        cls.product = Product.objects.create(name="Seats", sales_price=200)
        cls.other = Product.objects.create(name="Support", sales_price=50)
        cls.sub = Subscription.objects.create(customer=cls.customer, plan=cls.plan,
                                              status="active", next_billing_date=cls.today)
        SubscriptionLine.objects.create(subscription=cls.sub, product=cls.product, quantity=2)
        SubscriptionLine.objects.create(subscription=cls.sub, product=cls.other, quantity=1)

    def discount(self, **kwargs):
        data = dict(name="Offer", discount_type="percent", value=10, start_date=self.today)
        data.update(kwargs)
        return Discount.objects.create(**data)

    def test_expiry_inclusive_then_stops(self):
        discount = self.discount(end_date=self.today)
        first = billing.build_invoice(self.sub, self.today, today=self.today)
        second = billing.build_invoice(self.sub, self.today + timedelta(days=31), today=self.today + timedelta(days=1))
        self.assertEqual(first.discount_amount, D("55"))
        self.assertEqual(second.discount_amount, 0)
        discount.refresh_from_db()
        self.assertEqual(discount.times_used, 1)

    def test_future_inactive_and_zero_limit_do_not_apply(self):
        self.discount(start_date=self.today + timedelta(days=1))
        self.discount(is_active=False)
        self.discount(usage_limit=0)
        self.assertEqual(billing.build_invoice(self.sub, self.today).discount_amount, 0)

    def test_usage_limit_and_duplicate_do_not_consume_extra_use(self):
        discount = self.discount(usage_limit=1)
        self.assertEqual(billing.build_invoice(self.sub, self.today).discount_amount, D("55"))
        self.assertIsNone(billing.build_invoice(self.sub, self.today))
        self.assertEqual(billing.build_invoice(self.sub, self.today + timedelta(days=31)).discount_amount, 0)
        discount.refresh_from_db()
        self.assertEqual(discount.times_used, 1)

    def test_product_scope_excludes_other_products_and_plan_fee(self):
        discount = self.discount(value=25)
        discount.products.add(self.product)
        inv = billing.build_invoice(self.sub, self.today)
        self.assertEqual(inv.discount_amount, D("100"))
        self.assertEqual(inv.lines.get(product=self.other).discount, 0)
        self.assertEqual(inv.lines.get(product=None).discount, 0)

    def test_fixed_discount_capped_to_scoped_product(self):
        discount = self.discount(discount_type="fixed", value=1000)
        discount.products.add(self.other)
        self.assertEqual(billing.build_invoice(self.sub, self.today).discount_amount, D("50"))

    def test_expired_assigned_discount_falls_back_to_automatic(self):
        self.sub.discount = self.discount(start_date=self.today - timedelta(days=10), end_date=self.today - timedelta(days=1))
        self.sub.save(update_fields=["discount"])
        self.discount(value=20)
        self.assertEqual(billing.build_invoice(self.sub, self.today).discount_amount, D("110"))

    def test_fixed_rounding_conserves_cents_without_negative_lines(self):
        discount = self.discount(discount_type="fixed", value=D("0.03"))
        lines = [{"product_id": None, "subtotal": D("0.01")} for _ in range(6)]
        amounts = billing._discount_amounts(discount, lines)
        self.assertEqual(sum(amounts.values()), D("0.03"))
        self.assertTrue(all(0 <= amount <= D("0.01") for amount in amounts.values()))


class ConcurrentDiscountUsage(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_workers_share_last_discount_use_and_bill_period_once(self):
        today = timezone.localdate()
        customer = User.objects.create_user("parallel@customer.test")
        plan = Plan.objects.create(name="Monthly", price=100)
        discount = Discount.objects.create(name="Only once", discount_type="percent", value=10,
                                           start_date=today, usage_limit=1)
        subs = [Subscription.objects.create(customer=customer, plan=plan, status="active", next_billing_date=today) for _ in range(2)]
        barrier = Barrier(2)

        def worker(pk):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return len(billing.bill_subscription(Subscription(pk=pk)))
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, [s.pk for s in subs]))
        self.assertEqual(results, [1, 1])
        discount.refresh_from_db()
        self.assertEqual(discount.times_used, 1)
        self.assertEqual(Invoice.objects.filter(discount_amount__gt=0).count(), 1)
        # Both workers target the same new period: only one can advance its cursor.
        Subscription.objects.filter(pk=subs[0].pk).update(next_billing_date=today - timedelta(days=1))
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(worker, [subs[0].pk, subs[0].pk])), [0, 1])
