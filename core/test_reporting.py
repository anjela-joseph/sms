import csv
import io
from datetime import timedelta
from decimal import Decimal as D

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from .models import Invoice, Payment, Plan, Product, Subscription, SubscriptionLine
from .reporting import monthly_recurring_revenue


class Reporting(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.staff = User.objects.create_user("reports@staff.test", role="staff")
        cls.customer = User.objects.create_user("reports@customer.test", company='=HYPERLINK("bad")')

    def setUp(self):
        self.client.force_login(self.staff)

    def test_mrr_normalizes_periods_and_counts_fee_once(self):
        product = Product.objects.create(name="Seats", sales_price=10)
        monthly = Plan.objects.create(name="Monthly", price=100, billing_period="monthly")
        sub = Subscription.objects.create(customer=self.customer, plan=monthly, status="active", start_date=self.today)
        for _ in range(2):
            SubscriptionLine.objects.create(subscription=sub, product=product, quantity=2)
        for period, fee in (("yearly", 1200), ("weekly", 12), ("daily", 12)):
            plan = Plan.objects.create(name=period, price=fee, billing_period=period)
            Subscription.objects.create(customer=self.customer, plan=plan, status="active", start_date=self.today)
        for status in ("draft", "paused", "closed", "confirmed"):
            Subscription.objects.create(customer=self.customer, plan=monthly, status=status)
        Subscription.objects.create(customer=self.customer, plan=monthly, status="active", expiry_date=self.today)
        Subscription.objects.create(customer=self.customer, plan=monthly, status="active", start_date=self.today + timedelta(days=1))
        self.assertEqual(monthly_recurring_revenue(Subscription.objects.all()), D("657.00"))
        self.assertEqual(monthly_recurring_revenue(Subscription.objects.filter(plan=monthly)), D("140.00"))
        self.assertEqual(self.client.get("/dashboard/").context["kpi"]["mrr"], D("657.00"))

    def test_collected_revenue_by_plan_respects_dates_and_plan(self):
        plans = [Plan.objects.create(name=f"Plan {i}") for i in range(2)]
        for plan in plans:
            sub = Subscription.objects.create(customer=self.customer, plan=plan)
            inv = Invoice.objects.create(customer=self.customer, subscription=sub, due_date=self.today)
            Payment.objects.create(invoice=inv, date=self.today, amount=100, method="upi")
            Payment.objects.create(invoice=inv, date=self.today - timedelta(days=1), amount=50, method="cash")
        response = self.client.get("/dashboard/", {"from": self.today, "to": self.today, "plan": plans[0].pk})
        self.assertEqual(response.context["kpi"]["revenue"], 100)
        self.assertEqual(len(response.context["revenue_by_plan"]), 1)
        self.assertEqual(response.context["revenue_by_plan"][0]["revenue"], 100)
        for params in ({"plan": "bad"}, {"from": "bad"}, {"from": "2000-01-01", "to": "9999-12-31"}):
            self.assertEqual(self.client.get("/dashboard/", params).status_code, 200)

    def test_csv_uses_same_filters_exports_all_pages_and_escapes_text(self):
        Invoice.objects.bulk_create([Invoice(customer=self.customer, status="confirmed", due_date=self.today - timedelta(days=1),
                                             invoice_date=self.today, total=10) for _ in range(55)])
        Invoice.objects.create(customer=self.customer, status="draft", due_date=self.today)
        filters = {"status": "overdue", "q": "reports@customer", "date_from": self.today, "date_to": self.today, "page": 2}
        count = self.client.get("/invoices/", filters).context["page_obj"].paginator.count
        response = self.client.get("/invoices/export/", filters)
        data = b"".join(response.streaming_content).decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(data)))
        self.assertEqual(len(rows) - 1, count)
        self.assertEqual(count, 55)
        self.assertTrue(rows[1][1].startswith("'=HYPERLINK"))
        self.assertEqual(rows[1][-1], "10.00")
        self.assertEqual(self.client.get("/invoices/export/", {"date_from": "bad"}).status_code, 400)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get("/invoices/export/").status_code, 403)


class DemoSeed(TestCase):
    def test_seed_rerun_preserves_data_and_periods_are_contiguous(self):
        out = io.StringIO()
        call_command("seed_demo", subs=20, stdout=out)
        original = Invoice.objects.count()
        call_command("seed_demo", subs=20, stdout=out)
        self.assertEqual(Subscription.objects.count(), 20)
        self.assertEqual(Invoice.objects.count(), original)
        self.assertIn("no data changed", out.getvalue())
        for sub in Subscription.objects.select_related("plan").prefetch_related("invoices__lines"):
            invoices = sorted(sub.invoices.all(), key=lambda inv: inv.period_start)
            for index, inv in enumerate(invoices):
                self.assertEqual(sum(line.total for line in inv.lines.all()), inv.total)
                next_start = sub.plan.next_date(inv.period_start)
                self.assertEqual(inv.period_end, next_start - timedelta(days=1))
                self.assertEqual(invoices[index + 1].period_start if index + 1 < len(invoices) else sub.next_billing_date, next_start)
