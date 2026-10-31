from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import override_settings

from . import billing
from .billing_jobs import process_billing_run
from .models import BillingRun, Invoice
from .tests import Base


class BillingReports(Base):
    def test_cancelled_period_cannot_be_billed_again(self):
        invoice = billing.bill_subscription(self.sub)[0]
        billing.cancel_invoice(invoice)
        self.assertIsNone(billing.build_invoice(self.sub, self.today))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.create(subscription=self.sub, customer=self.cust,
                                   period_start=self.today, due_date=self.today)

    def test_unexpected_integrity_error_keeps_billing_cursor(self):
        with patch("core.billing.InvoiceLine.objects.bulk_create", side_effect=IntegrityError("broken line")):
            with self.assertRaises(IntegrityError):
                billing.bill_subscription(self.sub)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.next_billing_date, self.today)
        self.assertFalse(Invoice.objects.exists())

    def test_existing_period_advances_and_catches_up(self):
        start = self.today - timedelta(days=70)
        self.sub.next_billing_date = start
        self.sub.save(update_fields=["next_billing_date"])
        billing.build_invoice(self.sub, start)
        billing.bill_subscription(self.sub)
        periods = list(Invoice.objects.order_by("period_start").values_list("period_start", flat=True))
        self.assertEqual(periods, [start, self.plan.next_date(start), self.plan.next_date(self.plan.next_date(start))])

    def test_result_reports_actual_second_run_zero(self):
        self.cust.role = "staff"
        self.cust.save(update_fields=["role"])
        self.client.force_login(self.cust)
        with patch("core.billing_jobs.launch_billing") as launch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post("/billing/run/")
            run = BillingRun.objects.get()
            launch.assert_called_once_with(run.pk)
        self.assertRedirects(response, f"/billing/runs/{run.pk}/")
        process_billing_run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(run.invoice_ids), 1)
        self.assertContains(self.client.get(response.url), self.sub.number)
        second = BillingRun.objects.create()
        process_billing_run(second.pk)
        self.assertContains(self.client.get(f"/billing/runs/{second.pk}/"), "0 invoices created")
        process_billing_run(run.pk)  # completed job is immutable on a retry
        run.refresh_from_db()
        self.assertEqual(len(run.invoice_ids), 1)

    def test_customers_cannot_start_or_read_billing_runs(self):
        self.client.force_login(self.cust)
        run = BillingRun.objects.create()
        self.assertEqual(self.client.post("/billing/run/").status_code, 403)
        self.assertEqual(self.client.get(f"/billing/runs/{run.pk}/").status_code, 403)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_invoice_email_has_recipient_link_and_sent_timestamp(self):
        inv = billing.bill_subscription(self.sub)[0]
        billing.send_invoice(inv)
        self.assertEqual(mail.outbox[0].to, [self.cust.email])
        self.assertIn(f"/invoices/{inv.pk}/", mail.outbox[0].body)
        inv.refresh_from_db()
        self.assertIsNotNone(inv.sent_at)

    def test_failed_email_is_not_marked_sent(self):
        inv = billing.bill_subscription(self.sub)[0]
        with patch("core.billing.send_mail", side_effect=OSError("SMTP unavailable")):
            with self.assertRaises(OSError):
                billing.send_invoice(inv)
        inv.refresh_from_db()
        self.assertIsNone(inv.sent_at)
