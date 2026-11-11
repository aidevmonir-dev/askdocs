from django.core.management.base import BaseCommand

from rag.services.ingest import purge_expired


class Command(BaseCommand):
    help = "Delete visitor workspaces older than RETENTION_HOURS. Safe to run from cron."

    def handle(self, *args, **options):
        self.stdout.write(f"Deleted {purge_expired()} rows from expired workspaces.")
