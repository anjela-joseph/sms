"""Demo data: catalog, plans, templates, discounts, users and N subscriptions with invoice history.

    python manage.py seed_demo                 # 10,000 subscriptions
    python manage.py seed_demo --subs 500      # smaller
    python manage.py seed_demo --flush         # wipe billing data first

Logins: admin@brightloop.test / Admin@12345, staff@brightloop.test / Staff@12345,
        demo@customer.test / Demo@12345
"""
import random
import time
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from core.models import (Discount, Invoice, InvoiceLine, Payment, Plan, Product, ProductVariant,
                         QuotationTemplate, Subscription, SubscriptionLine, Tax, TemplateLine,
                         add_months, money)

D = Decimal


def prev_date(plan, d):
    return {"daily": lambda: d - timedelta(days=1), "weekly": lambda: d - timedelta(weeks=1),
            "monthly": lambda: add_months(d, -1), "yearly": lambda: add_months(d, -12)}[plan.billing_period]()


class Command(BaseCommand):
    help = "Load demo data (fast, bulk inserts)."

    def add_arguments(self, parser):
        parser.add_argument("--subs", type=int, default=10_000)
        parser.add_argument("--flush", action="store_true")

    @transaction.atomic
    def handle(self, *args, subs, flush, **_):
        t0 = time.perf_counter()
        rnd = random.Random(42)
        today = timezone.localdate()
        if flush:
            for m in (Payment, InvoiceLine, Invoice, SubscriptionLine, Subscription, TemplateLine,
                      QuotationTemplate, Discount, ProductVariant, Product, Plan, Tax):
                m.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()

        # ---- users
        admin, _ = User.objects.get_or_create(email="admin@brightloop.test", defaults=dict(
            role=User.Role.ADMIN, is_staff=True, is_superuser=True, first_name="Asha", last_name="Admin"))
        admin.set_password("Admin@12345"); admin.save()
        staff, _ = User.objects.get_or_create(email="staff@brightloop.test", defaults=dict(
            role=User.Role.STAFF, is_staff=True, first_name="Sam", last_name="Sales"))
        staff.set_password("Staff@12345"); staff.save()
        demo, _ = User.objects.get_or_create(email="demo@customer.test", defaults=dict(
            company="Demo Traders", first_name="Dev", last_name="Customer"))
        demo.set_password("Demo@12345"); demo.save()

        # ---- catalog
        gst18 = Tax.objects.create(name="GST", rate=D("18.00"))
        Tax.objects.create(name="GST reduced", rate=D("5.00"))
        crm = Product.objects.create(name="Brightloop CRM", sales_price=D("1200"), cost_price=D("300"),
                                     default_tax=gst18, notes="Core business software, per seat")
        odoo = ProductVariant.objects.create(product=crm, attribute="Brand", value="Odoo", extra_price=D("560"))
        ProductVariant.objects.create(product=crm, attribute="Brand", value="Standard", extra_price=D("0"))
        analytics = Product.objects.create(name="Analytics add-on", product_type=Product.Type.ADDON,
                                           sales_price=D("450"), cost_price=D("80"), default_tax=gst18)
        support = Product.objects.create(name="Priority support", product_type=Product.Type.ADDON,
                                         sales_price=D("900"), cost_price=D("400"), default_tax=gst18)
        products = [crm, analytics, support]

        start = add_months(today, -24)
        monthly = Plan.objects.create(name="Monthly Standard", price=D("0"), billing_period="monthly",
                                      start_date=start, pausable=True)
        yearly = Plan.objects.create(name="Yearly Enterprise", price=D("5000"), billing_period="yearly",
                                     min_quantity=10, start_date=start, closable=False)
        weekly = Plan.objects.create(name="Weekly Starter", price=D("99"), billing_period="weekly",
                                     start_date=start, auto_close=True, renewable=False)
        plans = [monthly] * 7 + [yearly] * 2 + [weekly]

        t_small = QuotationTemplate.objects.create(name="Small shop – monthly", plan=monthly,
                                                   validity_days=15, duration_periods=12)
        TemplateLine.objects.create(template=t_small, product=crm, quantity=3)
        t_big = QuotationTemplate.objects.create(name="Enterprise – yearly", plan=yearly,
                                                 validity_days=30, duration_periods=3)
        TemplateLine.objects.create(template=t_big, product=crm, variant=odoo, quantity=25)
        TemplateLine.objects.create(template=t_big, product=support, quantity=1)

        Discount.objects.create(name="Bulk seats", discount_type="percent", value=D("10"), min_quantity=20,
                                start_date=start)
        Discount.objects.create(name="Diwali offer", discount_type="fixed", value=D("500"),
                                min_purchase=D("5000"), start_date=today, end_date=today + timedelta(days=15),
                                usage_limit=100)

        # ---- customers (hash once, reuse: hashing 2k passwords would take minutes)
        pwd = make_password("Demo@12345")
        n_cust = max(1, subs // 5)
        User.objects.bulk_create([User(email=f"customer{i}@example.test", password=pwd, company=f"Shop {i}",
                                       role=User.Role.CUSTOMER) for i in range(n_cust)], batch_size=2000)
        customers = list(User.objects.filter(role=User.Role.CUSTOMER).values_list("id", flat=True))

        # ---- subscriptions
        statuses = ["active"] * 80 + ["closed"] * 8 + ["paused"] * 4 + ["quotation"] * 4 + ["draft"] * 4
        sub_objs, meta = [], []
        for i in range(subs):
            plan = rnd.choice(plans)
            status = rnd.choice(statuses)
            # ~5% of active subs are due today or overdue for billing -> "billing day" demo
            if status == "active" and rnd.random() < 0.05:
                nbd = today - timedelta(days=rnd.randint(0, 2))
            else:
                step = {"daily": 1, "weekly": 7, "monthly": 28, "yearly": 300}[plan.billing_period]
                nbd = today + timedelta(days=rnd.randint(1, step))
            history = rnd.randint(1, 4) if status in ("active", "closed", "paused") else 0
            s = nbd
            for _ in range(history):
                s = prev_date(plan, s)
            sub_objs.append(Subscription(
                number=f"SUB{i + 1:06d}", customer_id=demo.id if i < 3 else rnd.choice(customers), plan=plan,
                start_date=s if history else today, status=status,
                expiry_date=add_months(s, 24) if history else None,
                next_billing_date=nbd if history else None, payment_terms=rnd.choice([0, 15, 30]),
                salesperson_id=staff.id))
            meta.append((plan, history, s))
        Subscription.objects.bulk_create(sub_objs, batch_size=2000)

        # ---- lines, invoice history, payments
        line_objs, inv_objs, inv_lines, pay_plan = [], [], [], []
        for sub, (plan, history, s) in zip(sub_objs, meta):
            qty = rnd.randint(plan.min_quantity, plan.min_quantity + 30)
            prod = rnd.choice(products)
            unit = prod.sales_price + (odoo.extra_price if prod is crm and rnd.random() < 0.3 else 0)
            line_objs.append(SubscriptionLine(subscription=sub, product=prod, quantity=qty, unit_price=unit, tax=gst18))
            untaxed = money(unit * qty + plan.price)
            tax = money(untaxed * D("0.18"))
            p = s
            for k in range(history):
                inv_date, latest = p, k == history - 1
                due = inv_date + timedelta(days=sub.payment_terms)
                paid = not latest or rnd.random() < 0.7
                inv_objs.append(Invoice(subscription=sub, customer_id=sub.customer_id, invoice_date=inv_date,
                                        due_date=due, period_start=p, period_end=plan.next_date(p) - timedelta(days=1),
                                        status="paid" if paid else "confirmed", untaxed_amount=untaxed,
                                        tax_amount=tax, total=untaxed + tax, amount_paid=untaxed + tax if paid else 0))
                inv_lines.append((prod, qty, unit, untaxed, tax))
                pay_plan.append(inv_date + timedelta(days=rnd.randint(0, max(1, sub.payment_terms))) if paid else None)
                p = plan.next_date(p)
        SubscriptionLine.objects.bulk_create(line_objs, batch_size=5000)
        Invoice.objects.bulk_create(inv_objs, batch_size=5000)
        for inv in inv_objs:
            inv.number = f"INV/{inv.invoice_date.year}/{inv.pk:06d}"
        Invoice.objects.bulk_update(inv_objs, ["number"], batch_size=5000)
        InvoiceLine.objects.bulk_create([
            InvoiceLine(invoice=inv, product=prod, description=prod.name, quantity=qty, unit_price=unit,
                        subtotal=untaxed, tax_rate=D("18"), tax_amount=tax)
            for inv, (prod, qty, unit, untaxed, tax) in zip(inv_objs, inv_lines)], batch_size=5000)
        methods = [m for m, _ in Payment.Method.choices]
        Payment.objects.bulk_create([
            Payment(invoice=inv, amount=inv.total, method=rnd.choice(methods), date=min(d, today),
                    note="Seeded", recorded_by=staff)
            for inv, d in zip(inv_objs, pay_plan) if d], batch_size=5000)

        due_now = Subscription.objects.filter(status="active", next_billing_date__lte=today).count()
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {subs} subscriptions, {len(inv_objs)} invoices, {len(customers)} customers in "
            f"{time.perf_counter() - t0:.1f}s. {due_now} subscriptions are due for billing today."))
