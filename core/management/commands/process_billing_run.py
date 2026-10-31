from django.core.management.base import BaseCommand
from core.billing_jobs import process_billing_run


class Command(BaseCommand):
    help = "Process a queued billing run (normally launched by the Run billing button)."

    def add_arguments(self, parser):
        parser.add_argument("run_id", type=int)

    def handle(self, *args, **options):
        process_billing_run(options["run_id"])
