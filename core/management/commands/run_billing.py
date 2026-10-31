"""Billing run. Safe to re-run: the unique (subscription, period_start) constraint blocks double billing.

Cron (daily 06:00):  0 6 * * *  cd /app && python manage.py run_billing
"""
import time
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.billing import run_billing
from core.models import Invoice


class Command(BaseCommand):
    help = "Generate invoices for every active subscription that is due."

    def add_arguments(self, parser):
        parser.add_argument("--date", type=date.fromisoformat, help="Bill as of this date (YYYY-MM-DD)")
        parser.add_argument("--details", action="store_true", help="List created invoices and their subscription periods")

    def handle(self, *args, **opts):
        t0 = time.perf_counter()
        stats = run_billing(opts.get("date"))
        self.stdout.write(self.style.SUCCESS(
            f"{stats['invoices']} invoices for {stats['subscriptions']} subscriptions, "
            f"{stats['errors']} errors, in {time.perf_counter() - t0:.1f}s"))
        if opts["details"]:
            for invoice in Invoice.objects.filter(pk__in=stats["invoice_ids"]).select_related("subscription").iterator():
                self.stdout.write(f"{invoice.number} | {invoice.subscription.number} | "
                                  f"{invoice.period_start} to {invoice.period_end} | {invoice.total}")
        if stats["errors"]:
            raise CommandError(f"Failed subscription IDs: {stats['error_subscription_ids']}. Their billing dates were preserved.")
