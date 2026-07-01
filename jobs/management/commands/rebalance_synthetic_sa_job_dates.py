import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs.models import JobAdvert


class Command(BaseCommand):
    help = "Move a chosen number of synthetic South African MBA job dates into a target year for time-series testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=50000,
            help="Number of synthetic rows to move into the target year.",
        )
        parser.add_argument(
            "--from-year",
            type=int,
            default=2025,
            help="Only rows currently in this year are eligible to move.",
        )
        parser.add_argument(
            "--to-year",
            type=int,
            default=2024,
            help="Target year to assign across a random spread of dates.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=20240701,
            help="Random seed for repeatable date redistribution.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=2000,
            help="Rows to update per bulk batch.",
        )

    def handle(self, *args, **options):
        count = max(1, int(options["count"]))
        batch_size = max(100, int(options["batch_size"]))
        from_year = int(options["from_year"])
        to_year = int(options["to_year"])
        if from_year == to_year:
            raise CommandError("--from-year and --to-year must be different.")
        rng = random.Random(int(options["seed"]))
        start_of_target_year = date(to_year, 1, 1)
        end_of_target_year = date(to_year, 12, 31)

        candidates = list(
            JobAdvert.objects.filter(
                external_id__startswith="synthetic-sa-mba-",
                date_posted__year=from_year,
            ).only("id", "date_posted", "source_payload").order_by("id")[:count]
        )
        if not candidates:
            raise CommandError(f"No synthetic {from_year} rows were found to rebalance.")

        updates = []
        for job in candidates:
            days = (end_of_target_year - start_of_target_year).days
            new_date = start_of_target_year + timedelta(days=rng.randint(0, days))
            payload = dict(job.source_payload or {})
            payload["date_rebalanced_at"] = timezone.now().isoformat()
            payload["date_rebalanced_from_year"] = from_year
            payload["date_rebalanced_to_year"] = to_year
            job.date_posted = new_date
            job.source_payload = payload
            updates.append(job)

        updated = 0
        for start in range(0, len(updates), batch_size):
            batch = updates[start:start + batch_size]
            JobAdvert.objects.bulk_update(batch, ["date_posted", "source_payload"], batch_size=batch_size)
            updated += len(batch)
            self.stdout.write(f"Updated {updated}/{len(updates)} synthetic rows into 2024...")

        self.stdout.write(
            self.style.SUCCESS(
                f"Moved {updated} synthetic South African MBA job dates from {from_year} into {to_year}."
            )
        )
