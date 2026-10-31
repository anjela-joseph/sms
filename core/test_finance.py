from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from .models import Invoice, Plan, Subscription


class FinancePages(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.staff = User.objects.create_user("staff@finance.test", role="staff")
        cls.customer = User.objects.create_user("buyer@finance.test", company="Acme", first_name="Ada", last_name="Lovelace")
        cls.plan = Plan.objects.create(name="Monthly")
        cls.sub = Subscription.objects.create(customer=cls.customer, plan=cls.plan)
        cls.invoices = {}
        for index, status in enumerate(("paid", "confirmed", "confirmed", "draft", "cancelled")):
            inv = Invoice.objects.create(
                customer=cls.customer, subscription=cls.sub, status=status,
                invoice_date=cls.today - timedelta(days=index),
                period_start=cls.today - timedelta(days=index),
                due_date=cls.today - timedelta(days=1) if index == 2 else cls.today,
                total=Decimal("100"), amount_paid=Decimal("100") if status == "paid" else Decimal("0"))
            cls.invoices[("paid", "outstanding", "overdue", "draft", "cancelled")[index]] = inv

    def setUp(self):
        self.client.force_login(self.staff)

    def test_status_filters_and_detail_links(self):
        for status, inv in self.invoices.items():
            with self.subTest(status=status):
                response = self.client.get("/invoices/", {"status": status})
                self.assertEqual(list(response.context["page_obj"]), [inv])
                self.assertContains(response, f'/invoices/{inv.pk}/')

    def test_customer_and_number_search(self):
        for term in ("acme", "ada", "lovelace", "Ada Lovelace", "buyer@finance.test"):
            self.assertEqual(self.client.get("/invoices/", {"q": term}).context["page_obj"].paginator.count, 5)
        inv = self.invoices["paid"]
        self.assertEqual(list(self.client.get("/invoices/", {"q": inv.number}).context["page_obj"]), [inv])

    def test_inclusive_dates_and_invalid_filters(self):
        response = self.client.get("/invoices/", {"date_from": self.today, "date_to": self.today})
        self.assertEqual(list(response.context["page_obj"]), [self.invoices["paid"]])
        for params in ({"date_from": "bad"}, {"date_from": self.today, "date_to": self.today - timedelta(days=1)}, {"status": "unknown"}):
            response = self.client.get("/invoices/", params)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)

    def test_pagination_keeps_filters_and_avoids_n_plus_one(self):
        Invoice.objects.bulk_create([Invoice(customer=self.customer, due_date=self.today, status="draft") for _ in range(55)])
        with self.assertNumQueries(4):  # session, user, count, joined invoice page
            response = self.client.get("/invoices/", {"status": "draft"})
            self.assertEqual(len(response.context["page_obj"]), 50)
        self.assertContains(response, "status=draft&amp;page=2")
        self.assertEqual(len(self.client.get("/invoices/", {"status": "draft", "page": 2}).context["page_obj"]), 6)

    def test_customer_cannot_list_finances(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get("/invoices/").status_code, 403)
