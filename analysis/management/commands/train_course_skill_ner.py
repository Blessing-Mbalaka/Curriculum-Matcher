from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analysis.course_skill_ner import collect_training_examples, train_course_skill_ner


class Command(BaseCommand):
    help = "Fine-tune a spaCy NER model for SKILL extraction using labelled course module skill entities."

    def add_arguments(self, parser):
        parser.add_argument("--base-model", default=getattr(settings, "SPACY_MODEL_NAME", "en_core_web_sm"))
        parser.add_argument("--output", default=getattr(settings, "COURSE_SKILL_NER_MODEL_PATH", "models/course_skill_ner"))
        parser.add_argument("--epochs", type=int, default=30)
        parser.add_argument("--dropout", type=float, default=0.2)
        parser.add_argument("--dev-ratio", type=float, default=0.2)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--min-examples", type=int, default=5)
        parser.add_argument("--reviewed-only", action="store_true")
        parser.add_argument("--dry-run", action="store_true", help="Validate available training rows without training.")

    def handle(self, *args, **options):
        examples, skipped = collect_training_examples(reviewed_only=options["reviewed_only"])
        if len(examples) < options["min_examples"]:
            raise CommandError(
                f"Only {len(examples)} usable labelled modules found; need at least {options['min_examples']}. "
                "Review course skill rows in Data Export or add more parsed course files first."
            )

        self.stdout.write(f"Prepared {len(examples)} usable examples ({skipped} entities skipped).")
        if options["dry_run"]:
            return

        try:
            result = train_course_skill_ner(
                base_model=options["base_model"],
                output=options["output"],
                epochs=options["epochs"],
                dropout=options["dropout"],
                dev_ratio=options["dev_ratio"],
                seed=options["seed"],
                min_examples=options["min_examples"],
                reviewed_only=options["reviewed_only"],
                progress_callback=self.stdout.write,
            )
        except ImportError as exc:
            raise CommandError("spaCy is required to fine-tune the course skill NER model.") from exc
        except OSError as exc:
            raise CommandError(f"Could not load spaCy base model '{options['base_model']}'.") from exc

        if not result["trained"]:
            raise CommandError(result["reason"])
        self.stdout.write(self.style.SUCCESS(f"Saved fine-tuned course skill NER model to {result['output']}"))
