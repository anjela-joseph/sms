from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from accounts.validators import StrongPasswordValidator
from core import billing
from core.models import Discount, Invoice, Plan, Product, ProductVariant, Subscription, SubscriptionLine, Tax


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.cust = User.objects.create_user("c@x.test", "Good@Pass1")
        self.other = User.objects.create_user("o@x.test", "Good@Pass1")
        self.tax = Tax.objects.create(name="GST", rate=D("18"))
        self.prod = Product.objects.create(name="CRM", sales_price=D("1000"), default_tax=self.tax)
        self.odoo = ProductVariant.objects.create(product=self.prod, attribute="Brand", value="Odoo", extra_price=D("560"))
        self.plan = Plan.objects.create(name="Monthly", billing_period="monthly", start_date=self.today - timedelta(days=400))
        self.sub = Subscription.objects.create(customer=self.cust, plan=self.plan, start_date=self.today)
        SubscriptionLine.objects.create(subscription=self.sub, product=self.prod, variant=self.odoo, quantity=2)
        for st in ("quotation", "confirmed", "active"):
            billing.change_status(self.sub, st)


class Rules(Base):
    def test_password_rule(self):
        v = StrongPasswordValidator()
        for bad in ("Short@1", "alllower@123", "ALLUPPER@123", "NoSpecial123"):
            with self.assertRaises(ValidationError):
                v.validate(bad)
        v.validate("Good@Pass1")

    def test_variant_price_and_tax(self):
        inv = billing.bill_subscription(self.sub)[0]
        self.assertEqual(inv.untaxed_amount, D("3120.00"))  # (1000 + 560) x 2
        self.assertEqual(inv.tax_amount, D("561.60"))
        self.assertEqual(inv.total, D("3681.60"))

    def test_never_billed_twice(self):
        self.assertEqual(billing.run_billing()["invoices"], 1)
        self.assertEqual(billing.run_billing()["invoices"], 0)
        self.assertIsNone(billing.build_invoice(self.sub, self.today))  # DB constraint backstop
        self.assertEqual(Invoice.objects.count(), 1)

    def test_catch_up_skips_nothing(self):
        Subscription.objects.filter(pk=self.sub.pk).update(next_billing_date=self.today - timedelta(days=70))
        made = billing.bill_subscription(self.sub)
        self.assertEqual(len(made), 3)  # three missed monthly periods, each billed once

    def test_discount_auto_applies_and_respects_usage_limit(self):
        d = Discount.objects.create(name="Seats", discount_type="percent", value=D("10"), min_quantity=2,
                                    start_date=self.today, usage_limit=1)
        inv = billing.bill_subscription(self.sub)[0]
        self.assertEqual(inv.discount_amount, D("312.00"))
        d.refresh_from_db(); self.assertEqual(d.times_used, 1)
        inv2 = billing.build_invoice(self.sub, self.today + timedelta(days=31))
        self.assertEqual(inv2.discount_amount, D("0"))  # limit used up

    def test_payment_marks_paid(self):
        inv = billing.bill_subscription(self.sub)[0]
        billing.record_payment(inv, D("1000"), "upi")
        inv.refresh_from_db(); self.assertEqual(inv.status, "confirmed")
        billing.record_payment(inv, inv.amount_due, "bank")
        inv.refresh_from_db(); self.assertEqual(inv.status, "paid")
        with self.assertRaises(ValidationError):
            billing.record_payment(inv, D("1"), "cash")

    def test_bad_transition_rejected(self):
        s = Subscription.objects.create(customer=self.cust, plan=self.plan)
        with self.assertRaises(ValidationError):
            billing.change_status(s, "active")  # draft -> active skips steps


class Access(Base):
    def test_customer_sees_only_own_invoice(self):
        inv = billing.bill_subscription(self.sub)[0]
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/invoices/{inv.pk}/").status_code, 403)
        self.client.force_login(self.cust)
        self.assertEqual(self.client.get(f"/invoices/{inv.pk}/").status_code, 200)

    def test_only_admin_adds_staff(self):
        staff = User.objects.create_user("s@x.test", "Good@Pass1", role="staff")
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/staff/new/").status_code, 403)
        self.client.force_login(self.cust)
        self.assertEqual(self.client.get("/dashboard/").status_code, 403)

    def test_signup_rejects_duplicate_email_and_weak_password(self):
        r = self.client.post("/signup/", {"email": "C@x.test", "password1": "Good@Pass1", "password2": "Good@Pass1"})
        self.assertContains(r, "already exists")
        r = self.client.post("/signup/", {"email": "n@x.test", "password1": "weakpass", "password2": "weakpass"})
        self.assertContains(r, "uppercase")
