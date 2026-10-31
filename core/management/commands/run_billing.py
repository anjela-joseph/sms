"""Billing run. Safe to re-run: the unique (subscription, period_start) constraint blocks double billing.

Cron (daily 06:00):  0 6 * * *  cd /app && python manage.py run_billing
"""
import time
from datetime import date

from django.core.management.base import BaseCommand

from core.billing import run_billing


class Command(BaseCommand):
    help = "Generate invoices for every active subscription that is due."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=date.fromisoformat, help="Bill as of this date (YYYY-MM-DD)")

    def handle(self, *args, **opts):
        t0 = time.perf_counter()
        stats = run_billing(opts.get("date"))
        self.stdout.write(self.style.SUCCESS(
            f"{stats['invoices']} invoices for {stats['subscriptions']} subscriptions, "
            f"{stats['errors']} errors, in {time.perf_counter() - t0:.1f}s"))
