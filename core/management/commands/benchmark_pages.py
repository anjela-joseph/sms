"""Measure complete Django responses against the current demo database."""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from core.models import BillingRun, Discount, Invoice, Plan, Subscription


class Command(BaseCommand):
    help = "Check rendered app pages and full CSV downloads against the 2-second budget."

    def add_arguments(self, parser):
        parser.add_argument("--runs", type=int, default=3)
        parser.add_argument("--budget", type=float, default=2.0)

    def handle(self, *args, runs, budget, **options):
        if runs < 1 or budget <= 0:
            raise CommandError("--runs and --budget must be positive.")
        admin = User.objects.filter(role="admin").first()
        customer = User.objects.filter(email="demo@customer.test").first()
        invoice = Invoice.objects.first()
        discount = Discount.objects.first()
        plan = Plan.objects.first()
        run = BillingRun.objects.order_by("-pk").first()
        if not all((admin, customer, invoice, discount, plan)):
            raise CommandError("Run migrate and seed_demo first.")
        self.stdout.write(f"Database: {connection.vendor}; subscriptions: {Subscription.objects.count()}; invoices: {Invoice.objects.count()}")
        pages = [
            (admin, "/dashboard/"), (admin, f"/dashboard/?plan={plan.pk}"),
            (admin, "/invoices/"), (admin, "/invoices/?status=overdue"),
            (admin, "/invoices/?q=customer&status=paid&page=20"),
            (admin, "/invoices/?status=draft"), (admin, "/invoices/?status=cancelled"),
            (admin, f"/invoices/{invoice.pk}/"), (admin, "/invoices/export/"),
            (admin, "/invoices/export/?status=overdue"),
            (admin, "/payments/"), (admin, "/payments/?method=upi"),
            (admin, "/discounts/"), (admin, "/discounts/new/"),
            (admin, f"/discounts/{discount.pk}/edit/"),
            (admin, "/subscriptions/new/"), (admin, "/staff/new/"),
            (customer, "/portal/"), (None, "/login/"), (None, "/signup/"),
            (None, "/password-reset/"),
        ]
        if run:
            pages.append((admin, f"/billing/runs/{run.pk}/"))
        failed = []
        for user, url in pages:
            client = Client()
            if user:
                client.force_login(user)
            timings, query_counts = [], []
            for _ in range(runs):
                with CaptureQueriesContext(connection) as queries:
                    start = time.perf_counter()
                    response = client.get(url)
                    if response.streaming:
                        for chunk in response.streaming_content:
                            pass
                    else:
                        _ = response.content
                    elapsed = time.perf_counter() - start
                    response.close()
                if response.status_code != 200:
                    raise CommandError(f"{url} returned HTTP {response.status_code}")
                timings.append(elapsed)
                query_counts.append(len(queries))
            worst = max(timings)
            self.stdout.write(f"{worst * 1000:8.1f} ms  {max(query_counts):3} queries  {url}")
            if worst >= budget:
                failed.append(url)
        if failed:
            raise CommandError(f"Exceeded {budget}s: {', '.join(failed)}")
        self.stdout.write(self.style.SUCCESS(f"All {len(pages)} routes passed {runs} runs each under {budget}s (full responses; excludes browser/network)."))
