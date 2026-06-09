import csv
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from analysis.course_skill_ner import ensure_course_skill_ner_model
from analysis.spacyskillextraction import SpacySkillExtractor
from courses.models import Module
from jobs.models import JobAdvert


CSV_HEADERS = [
    "source_type", "source_id", "parent_label", "source_label", "entity_id",
    "chunk_id", "skill", "label", "skill_type", "tier", "source",
    "confidence", "mention_count", "text", "start", "end", "label_status",
]


class Command(BaseCommand):
    help = "Train/check course skill NER, re-extract stored skills, update records, and write CSV audit files."

    def add_arguments(self, parser):
        parser.add_argument("--courses-only", action="store_true", help="Only clean/refine course module skills.")
        parser.add_argument("--jobs-only", action="store_true", help="Only clean/refine job advert skills.")
        parser.add_argument("--force-train", action="store_true", help="Train the course skill NER model even if it is current.")
        parser.add_argument("--dry-run", action="store_true", help="Write CSVs without updating stored skill fields.")
        parser.add_argument("--output-dir", default="csv", help="Folder for CSV outputs. Default: csv")

    def handle(self, *args, **options):
        process_courses = options["courses_only"] or not options["jobs_only"]
        process_jobs = options["jobs_only"] or not options["courses_only"]
        output_dir = Path(options["output_dir"])
        if not output_dir.is_absolute():
            output_dir = Path(settings.BASE_DIR) / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        self.stdout.write("Checking course skill NER training data/model...")
        train_result = ensure_course_skill_ner_model(
            force=options["force_train"],
            progress_callback=self.stdout.write,
        )
        if train_result.get("trained"):
            self.stdout.write(self.style.SUCCESS(
                f"Trained course skill NER model using {train_result['train_examples']} examples."
            ))
        else:
            self.stdout.write(f"Training skipped: {train_result.get('reason', 'not needed')}")

        extractor = SpacySkillExtractor()
        self.stdout.write(f"Using skill extractor backend: {extractor.backend}")

        course_rows = []
        job_rows = []
        updated_modules = 0
        updated_jobs = 0

        if process_courses:
            updated_modules, course_rows = self._process_modules(extractor, dry_run=options["dry_run"])
            self._write_rows(output_dir / "refined-course-skills.csv", course_rows)

        if process_jobs:
            updated_jobs, job_rows = self._process_jobs(extractor, dry_run=options["dry_run"])
            self._write_rows(output_dir / "refined-job-skills.csv", job_rows)

        all_rows = course_rows + job_rows
        self._write_summary(output_dir / "refined-skill-summary.csv", all_rows)

        action = "Previewed" if options["dry_run"] else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{action} {updated_modules} modules and {updated_jobs} jobs. "
            f"Wrote CSV outputs to {output_dir}."
        ))

    def _process_modules(self, extractor, dry_run=False):
        rows = []
        updated = 0
        modules = Module.objects.select_related("course").exclude(content="").order_by("course__code", "order", "name", "id")
        for module in modules.iterator(chunk_size=100):
            entities = extractor.extract_entities(module.content, document_id=f"module-{module.id}")
            skills = sorted({entity["skill"] for entity in entities if entity.get("skill")})
            if not dry_run:
                Module.objects.filter(id=module.id).update(
                    skills_extracted=skills,
                    skill_entities=entities,
                )
            updated += 1
            for entity in entities:
                rows.append(self._entity_row(
                    "module",
                    module.id,
                    module.course.code or module.course.name,
                    module.name,
                    entity,
                ))
        return updated, rows

    def _process_jobs(self, extractor, dry_run=False):
        rows = []
        updated = 0
        jobs = JobAdvert.objects.exclude(description="").order_by("title", "id")
        for job in jobs.iterator(chunk_size=100):
            text = job.analysis_text()
            if not text.strip():
                continue
            entities = extractor.extract_entities(text, document_id=f"job-{job.id}")
            skills = sorted({entity["skill"] for entity in entities if entity.get("skill")})
            if not dry_run:
                JobAdvert.objects.filter(id=job.id).update(
                    skills_extracted=skills,
                    skill_entities=entities,
                )
            updated += 1
            source_label = f"{job.title} @ {job.company}" if job.company else job.title
            for entity in entities:
                rows.append(self._entity_row(
                    "job",
                    job.id,
                    job.category,
                    source_label,
                    entity,
                ))
        return updated, rows

    def _entity_row(self, source_type, source_id, parent_label, source_label, entity):
        return {
            "source_type": source_type,
            "source_id": source_id,
            "parent_label": parent_label,
            "source_label": source_label,
            "entity_id": entity.get("id", ""),
            "chunk_id": entity.get("chunk_id", ""),
            "skill": entity.get("skill", ""),
            "label": entity.get("label", "SKILL"),
            "skill_type": entity.get("skill_type", ""),
            "tier": entity.get("tier", ""),
            "source": entity.get("source", ""),
            "confidence": entity.get("confidence", ""),
            "mention_count": entity.get("mention_count", ""),
            "text": entity.get("text", ""),
            "start": entity.get("start", ""),
            "end": entity.get("end", ""),
            "label_status": entity.get("label_status", "machine"),
        }

    def _write_rows(self, path, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_summary(self, path, rows):
        source_counts = Counter(row["source_type"] for row in rows)
        skill_counts = Counter(row["skill"] for row in rows if row["skill"])
        type_counts = Counter(row["skill_type"] for row in rows if row["skill_type"])
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["section", "label", "count"])
            for label, count in sorted(source_counts.items()):
                writer.writerow(["source_type", label, count])
            for label, count in sorted(type_counts.items()):
                writer.writerow(["skill_type", label, count])
            for label, count in skill_counts.most_common():
                writer.writerow(["skill", label, count])
