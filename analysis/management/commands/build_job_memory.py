from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from analysis.job_memory import DEFAULT_MEMORY_PATH, write_jobs_memory
from jobs.models import JobAdvert


class Command(BaseCommand):
    help = "Build a compact Markdown memory file from stored job adverts for LLM cleaning/review."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500, help="Maximum jobs to include. Default: 500.")
        parser.add_argument("--all", action="store_true", help="Include all matching jobs.")
        parser.add_argument("--uncleaned-only", action="store_true", help="Only include jobs without cleaned_payload.")
        parser.add_argument("--output", default=str(DEFAULT_MEMORY_PATH), help="Output Markdown path.")
        parser.add_argument("--max-description-chars", type=int, default=1200, help="Description evidence chars per job.")

    def handle(self, *args, **options):
        qs = JobAdvert.objects.exclude(description="").order_by("id")
        if options["uncleaned_only"]:
            qs = qs.filter(cleaned_payload={})
        if not options["all"]:
            qs = qs[:max(1, int(options["limit"] or 500))]

        output = Path(options["output"])
        if not output.is_absolute():
            output = Path(settings.BASE_DIR) / output

        jobs = list(qs)
        path, char_count = write_jobs_memory(
            jobs,
            output_path=output,
            max_description_chars=max(200, int(options["max_description_chars"] or 1200)),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {len(jobs)} job advert(s) to {path} ({char_count} characters)."
        ))
