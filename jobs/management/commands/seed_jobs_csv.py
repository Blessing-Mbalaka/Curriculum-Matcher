import csv
import io
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from analysis.models import TaskRecord
from analysis.services import run_gap_analysis
from jobs.ingestion import import_from_csv
from jobs.models import JobAdvert


class Command(BaseCommand):
    help = "Seed job adverts from a local CSV file and optionally run gap analysis for dashboard data."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            default="course_scraper/jobsdata/mba_jobs_south_africa(in).csv",
            help="Path to the CSV file to import.",
        )
        parser.add_argument(
            "--clear-csv-jobs",
            action="store_true",
            help="Delete previously imported CSV jobs before importing the file again.",
        )
        parser.add_argument(
            "--skip-analysis",
            action="store_true",
            help="Import the CSV without running gap analysis.",
        )
        parser.add_argument(
            "--run-name",
            default="Local CSV Seed Analysis",
            help="Analysis run name to use when analysis is executed.",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=None,
            help="Optional cap for the number of jobs scored during analysis.",
        )
        parser.add_argument(
            "--import-limit",
            type=int,
            default=None,
            help="Optional cap for the number of CSV data rows to import from the file.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"])
        if not csv_path.is_absolute():
            csv_path = Path(settings.BASE_DIR) / csv_path
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        task = TaskRecord.objects.create(
            task_id=f"seed-jobs-csv:{csv_path.name}",
            run_name=f"Seed jobs from {csv_path.name}",
            status="STARTED",
            progress=5,
            notes="Preparing CSV seed import...",
        )

        try:
            if options["clear_csv_jobs"]:
                deleted, _ = JobAdvert.objects.filter(source="csv").delete()
                task.progress = 10
                task.notes = f"Cleared {deleted} existing CSV job rows. Importing fresh data..."
                task.save(update_fields=["progress", "notes", "updated_at"])

            csv_bytes = csv_path.read_bytes()
            if options["import_limit"]:
                csv_bytes = self._limited_csv_bytes(csv_bytes, max(1, int(options["import_limit"])))
            result = import_from_csv(csv_bytes)

            errors = result.get("errors") or []
            summary = (
                f"Imported CSV {csv_path.name}. Saved {result['saved']} jobs, "
                f"skipped {result['skipped']} duplicates/invalid rows."
            )
            if errors:
                summary += f" First error: {errors[0]}"

            task.progress = 55 if not options["skip_analysis"] else 100
            task.notes = summary if not options["skip_analysis"] else summary + " Analysis skipped."
            if options["skip_analysis"]:
                task.status = "SUCCESS"
                task.finished_at = timezone.now()
            task.save(update_fields=["progress", "notes", "status", "finished_at", "updated_at"])

            self.stdout.write(self.style.SUCCESS(summary))
            if errors:
                self.stdout.write(self.style.WARNING(f"Import reported {len(errors)} row error(s)."))

            if options["skip_analysis"]:
                return

            self.stdout.write("Running gap analysis for dashboard data...")

            def report(percent: int, message: str) -> None:
                mapped = 55 + int(45 * max(0, min(100, percent)) / 100)
                TaskRecord.objects.filter(id=task.id).update(
                    status="STARTED",
                    progress=mapped,
                    notes=message,
                    updated_at=timezone.now(),
                )
                self.stdout.write(message)

            run = run_gap_analysis(
                run_name=options["run_name"],
                progress_callback=report,
                max_jobs=options["max_jobs"],
            )

            final_notes = (
                f"{summary} Analysis run #{run.id} completed with status {run.status}."
            )
            TaskRecord.objects.filter(id=task.id).update(
                status="SUCCESS",
                progress=100,
                notes=final_notes,
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
            self.stdout.write(self.style.SUCCESS(final_notes))
        except Exception as exc:
            TaskRecord.objects.filter(id=task.id).update(
                status="FAILURE",
                notes=str(exc),
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
            raise

    def _limited_csv_bytes(self, csv_bytes: bytes, row_limit: int) -> bytes:
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = csv_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise CommandError("Could not decode CSV for --import-limit preview.")

        reader = csv.reader(io.StringIO(text))
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")

        for index, row in enumerate(reader):
            writer.writerow(row)
            if index >= row_limit:
                break

        return output.getvalue().encode("utf-8")
