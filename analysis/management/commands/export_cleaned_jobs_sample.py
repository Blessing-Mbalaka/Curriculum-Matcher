import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from jobs.models import JobAdvert


class Command(BaseCommand):
    help = "Export a small Markdown sample of Gemini-cleaned job adverts."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10, help="Number of cleaned jobs to export.")
        parser.add_argument("--output", default="memory/cleaned_jobs_sample.md", help="Output Markdown path.")

    def handle(self, *args, **options):
        output = Path(options["output"])
        if not output.is_absolute():
            output = Path(settings.BASE_DIR) / output
        output.parent.mkdir(parents=True, exist_ok=True)

        jobs = list(
            JobAdvert.objects.exclude(cleaned_payload={})
            .order_by("id")[: max(1, int(options["limit"] or 10))]
        )

        lines = [
            "# Cleaned Jobs Sample",
            "",
            f"Exported {len(jobs)} cleaned job advert(s).",
            "",
        ]
        for job in jobs:
            lines.extend([
                f"## Job {job.id}: {job.title}",
                "",
                f"Company: {job.company or 'Unknown'}",
                f"Location: {job.location or 'Unknown'}",
                f"Cleaned at: {job.cleaned_at.isoformat() if job.cleaned_at else 'Unknown'}",
                "",
                "```json",
                json.dumps(job.cleaned_payload, indent=2, ensure_ascii=False),
                "```",
                "",
                "Skills extracted:",
            ])
            skills = job.skills_extracted or []
            lines.extend(f"- {skill}" for skill in skills[:30])
            if not skills:
                lines.append("- None")
            lines.append("")

        output.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote cleaned sample to {output}"))
