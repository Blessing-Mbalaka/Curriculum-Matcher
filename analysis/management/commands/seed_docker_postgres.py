import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from courses.models import Course
from jobs.models import JobAdvert


class Command(BaseCommand):
    help = "Load the SQLite fixture into the current database once, skipping when seed records already exist."

    def add_arguments(self, parser):
        parser.add_argument("--fixture", default="sqlite_to_postgres.json")
        parser.add_argument("--check-only", action="store_true")

    def handle(self, *args, **options):
        fixture_path = Path(options["fixture"])
        if not fixture_path.is_absolute():
            fixture_path = Path(settings.BASE_DIR) / fixture_path

        if not fixture_path.exists():
            self.stdout.write(f"Seed fixture not found at {fixture_path}; skipping.")
            return

        signatures = self._load_signatures(fixture_path)
        if self._seed_present(signatures):
            self.stdout.write(self.style.SUCCESS("Seed data already present; skipping fixture import."))
            return

        if options["check_only"]:
            raise CommandError("Seed data is not present.")

        self.stdout.write(f"Loading seed fixture from {fixture_path}...")
        call_command("loaddata", str(fixture_path))

        if self._seed_present(signatures):
            self.stdout.write(self.style.SUCCESS("Seed fixture imported successfully."))
            return

        raise CommandError("Fixture import completed, but the seed data signature could not be verified.")

    def _load_signatures(self, fixture_path: Path):
        with fixture_path.open("r", encoding="utf-8") as fixture_file:
            rows = json.load(fixture_file)

        course_codes = []
        job_fingerprints = []
        for row in rows:
            model = row.get("model")
            fields = row.get("fields") or {}
            if model == "courses.course" and fields.get("code"):
                course_codes.append(fields["code"])
            elif model == "jobs.jobadvert" and fields.get("fingerprint") and len(job_fingerprints) < 25:
                job_fingerprints.append(fields["fingerprint"])

        return {
            "course_codes": course_codes,
            "job_fingerprints": job_fingerprints,
        }

    def _seed_present(self, signatures):
        course_codes = signatures["course_codes"]
        if course_codes and Course.objects.filter(code__in=course_codes).count() == len(set(course_codes)):
            return True

        job_fingerprints = signatures["job_fingerprints"]
        if job_fingerprints and JobAdvert.objects.filter(fingerprint__in=job_fingerprints).count() == len(set(job_fingerprints)):
            return True

        return False