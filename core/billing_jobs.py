"""Run slow billing outside the HTTP request; PostgreSQL locks protect concurrent runs."""
import subprocess
import sys
import time

from django.conf import settings
from django.utils import timezone

from . import billing
from .models import BillingRun


def launch_billing(run_id):
    try:
        with (settings.BASE_DIR / ".billing-worker.log").open("ab") as worker_log:
            subprocess.Popen(
                [sys.executable, str(settings.BASE_DIR / "manage.py"), "process_billing_run", str(run_id)],
                cwd=settings.BASE_DIR, stdin=subprocess.DEVNULL, stdout=worker_log,
                stderr=worker_log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        BillingRun.objects.filter(pk=run_id).update(
            status="failed", finished_at=timezone.now(),
            error_message="Worker could not start. Retry with the command shown below.")


def process_billing_run(run_id):
    # A command retried against the same run cannot start another worker.
    if not BillingRun.objects.filter(pk=run_id, status__in=["queued", "failed"]).update(status="running", error_message=""):
        return
    started = time.perf_counter()
    try:
        stats = billing.run_billing()
    except Exception:
        BillingRun.objects.filter(pk=run_id).update(
            status="failed", finished_at=timezone.now(),
            elapsed_seconds=time.perf_counter() - started,
            error_message="Billing worker failed. Check the database connection and retry billing.")
        raise
    BillingRun.objects.filter(pk=run_id).update(
        status="completed", subscriptions=stats["subscriptions"], invoice_ids=stats["invoice_ids"],
        error_subscription_ids=stats["error_subscription_ids"], finished_at=timezone.now(),
        elapsed_seconds=time.perf_counter() - started)
