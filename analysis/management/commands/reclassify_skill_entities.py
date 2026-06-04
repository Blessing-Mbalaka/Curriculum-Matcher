from django.core.management.base import BaseCommand

from analysis.spacyskillextraction import SpacySkillExtractor, classify_skill_text
from courses.models import Module
from jobs.models import JobAdvert


class Command(BaseCommand):
    help = "Reclassify stored job and module skill entities, converting legacy skills into entity rows."

    def add_arguments(self, parser):
        parser.add_argument("--jobs", action="store_true", help="Only reclassify job adverts.")
        parser.add_argument("--modules", action="store_true", help="Only reclassify course modules.")

    def handle(self, *args, **options):
        extractor = SpacySkillExtractor()
        process_jobs = options["jobs"] or not options["modules"]
        process_modules = options["modules"] or not options["jobs"]
        updated_jobs = self._update_queryset(JobAdvert.objects.all(), "job", extractor) if process_jobs else 0
        updated_modules = self._update_queryset(Module.objects.all(), "module", extractor) if process_modules else 0
        self.stdout.write(self.style.SUCCESS(
            f"Reclassified {updated_jobs} job adverts and {updated_modules} modules."
        ))

    def _update_queryset(self, queryset, source_type, extractor):
        updated = 0
        for obj in queryset.iterator(chunk_size=100):
            entities = list(getattr(obj, "skill_entities", None) or [])
            if not entities and getattr(obj, "skills_extracted", None):
                entities = [
                    {
                        "id": extractor._entity_id(extractor._canonical(skill)),
                        "chunk_id": f"{source_type}-{obj.id}-{extractor._entity_id(extractor._canonical(skill))}",
                        "skill": skill,
                        "label": "SKILL",
                        "text": skill,
                        "source": "legacy",
                        "confidence": None,
                        "mention_count": 1,
                        "label_status": "machine",
                    }
                    for skill in obj.skills_extracted
                    if skill
                ]
            changed = False
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                skill = entity.get("skill") or entity.get("text") or ""
                classification = classify_skill_text(
                    skill,
                    entity.get("text") or skill,
                    " ".join(mention.get("text", "") for mention in entity.get("mentions", []) if isinstance(mention, dict)),
                    entity.get("pattern", ""),
                    entity.get("source", ""),
                )
                if entity.get("skill_type") != classification["skill_type"] or entity.get("tier") != classification["tier"]:
                    changed = True
                entity["skill_type"] = classification["skill_type"]
                entity["tier"] = classification["tier"]
                entity["classification_scores"] = classification["scores"]
                entity.setdefault("label", "SKILL")
                entity.setdefault("id", extractor._entity_id(extractor._canonical(skill)))
                entity.setdefault("chunk_id", f"{source_type}-{obj.id}-{entity['id']}")
                entity.setdefault("label_status", "machine")
            if entities and (changed or not getattr(obj, "skill_entities", None)):
                obj.skill_entities = entities
                obj.skills_extracted = sorted({
                    entity.get("skill")
                    for entity in entities
                    if isinstance(entity, dict) and entity.get("skill") and entity.get("skill_type") != "exclude"
                })
                obj.save(update_fields=["skill_entities", "skills_extracted"])
                updated += 1
        return updated
