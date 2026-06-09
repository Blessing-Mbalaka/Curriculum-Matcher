import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from analysis.gemini_cleaning import GeminiCleaningError, clean_job_advert_with_gemini, gemini_is_configured
from analysis.job_memory import build_job_memory_block
from analysis.spacyskillextraction import SpacySkillExtractor
from jobs.models import JobAdvert


class Command(BaseCommand):
    help = "Clean and enrich stored job adverts using Gemini, then refresh extracted job skills."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25, help="Maximum jobs to clean. Default: 25.")
        parser.add_argument("--force", action="store_true", help="Re-clean jobs that already have cleaned_payload.")
        parser.add_argument("--dry-run", action="store_true", help="Call Gemini and print progress without saving.")
        parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to wait between Gemini requests.")
        parser.add_argument("--compact-memory", action="store_true", help="Send compact Markdown job memory to Gemini.")
        parser.add_argument("--max-description-chars", type=int, default=1200, help="Description chars in compact memory mode.")

    def handle(self, *args, **options):
        if not gemini_is_configured():
            raise CommandError("Missing GEMINI_API_KEY. Add it to .env and restart the command.")

        limit = max(1, int(options["limit"] or 25))
        qs = JobAdvert.objects.exclude(description="")
        if not options["force"]:
            qs = qs.filter(cleaned_payload={})
        jobs = list(qs.order_by("id")[:limit])

        if not jobs:
            self.stdout.write(self.style.SUCCESS("No job adverts need Gemini cleaning."))
            return

        extractor = SpacySkillExtractor()
        updated = 0
        failed = 0
        self.stdout.write(f"Cleaning {len(jobs)} job advert(s) with Gemini...")

        for index, job in enumerate(jobs, start=1):
            label = self._console_text(f"{job.title} @ {job.company or 'Unknown'}")
            try:
                compact_text = ""
                if options["compact_memory"]:
                    compact_text = build_job_memory_block(
                        job,
                        max_description_chars=max(200, int(options["max_description_chars"] or 1200)),
                    )
                cleaned = clean_job_advert_with_gemini(job, compact_text=compact_text)
                skills = self._skills_from_payload(cleaned)
                skill_text = "\n".join([
                    job.analysis_text(),
                    " ".join(skills),
                ])
                entities = extractor.extract_entities(skill_text, document_id=f"job-{job.id}")

                if not options["dry_run"]:
                    job.cleaned_payload = cleaned
                    job.data_quality_flags = cleaned.get("data_quality_flags", [])
                    job.cleaned_at = timezone.now()
                    job.skill_entities = entities
                    job.skills_extracted = sorted({entity["skill"] for entity in entities if entity.get("skill")})
                    job.save(update_fields=[
                        "cleaned_payload",
                        "data_quality_flags",
                        "cleaned_at",
                        "skill_entities",
                        "skills_extracted",
                    ])
                updated += 1
                self.stdout.write(self.style.SUCCESS(f"[{index}/{len(jobs)}] Cleaned {label}"))
            except GeminiCleaningError as exc:
                failed += 1
                message = self._console_text(str(exc))
                self.stdout.write(self.style.WARNING(f"[{index}/{len(jobs)}] Failed {label}: {message}"))
                if "HTTP 429" in message:
                    self.stdout.write(self.style.WARNING("Stopping early because Gemini reported quota/rate limiting."))
                    break

            if options["sleep"] and index < len(jobs):
                time.sleep(options["sleep"])

        action = "Previewed" if options["dry_run"] else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {updated} job advert(s); {failed} failed."))

    def _skills_from_payload(self, cleaned):
        skills = []
        for key in ("required_skills", "preferred_skills", "tools", "soft_skills"):
            skills.extend(cleaned.get(key) or [])
        return skills

    def _console_text(self, value):
        return str(value).encode("ascii", "replace").decode("ascii")
