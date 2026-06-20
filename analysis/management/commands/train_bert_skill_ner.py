from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Fine-tune a Hugging Face BERT-style token classifier for SKILL NER."

    def add_arguments(self, parser):
        parser.add_argument("--base-model", default="distilbert-base-uncased")
        parser.add_argument("--output", default="models/bert_skill_ner")
        parser.add_argument("--epochs", type=int, default=3)
        parser.add_argument("--batch-size", type=int, default=8)
        parser.add_argument("--lr", type=float, default=2e-5)
        parser.add_argument("--dev-ratio", type=float, default=0.15)
        parser.add_argument("--max-length", type=int, default=256)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--seed-json", default=None)
        parser.add_argument("--synthetic", action="store_true")
        parser.add_argument("--per-skill", type=int, default=6)
        parser.add_argument("--save-synthetic", default=None)
        parser.add_argument(
            "--reviewed-only",
            action="store_true",
            help="Train from human-reviewed DB skill entities only.",
        )
        parser.add_argument(
            "--courses-only",
            action="store_true",
            help="Use course module DB examples only.",
        )

    def handle(self, *args, **options):
        try:
            from train_bert_ner import _load_db_examples, _load_json_examples, train
        except (ImportError, SystemExit) as exc:
            raise CommandError(
                "BERT NER training dependencies are missing. Install requirements first."
            ) from exc

        if options["seed_json"]:
            seed_examples = _load_json_examples(options["seed_json"])
        else:
            seed_examples = _load_db_examples(
                reviewed_only=options["reviewed_only"],
                include_jobs=not options["courses_only"],
            )

        if not seed_examples and not options["synthetic"]:
            raise CommandError(
                "No seed examples found. Add reviewed module skill entities or use --synthetic."
            )

        train(
            base_model=options["base_model"],
            output=options["output"],
            epochs=options["epochs"],
            batch_size=options["batch_size"],
            learning_rate=options["lr"],
            dev_ratio=options["dev_ratio"],
            max_length=options["max_length"],
            seed=options["seed"],
            seed_examples=seed_examples,
            synthetic=options["synthetic"],
            per_skill=options["per_skill"],
            save_synthetic=options["save_synthetic"],
        )
        self.stdout.write(self.style.SUCCESS(f"Saved BERT skill NER model to {options['output']}"))
