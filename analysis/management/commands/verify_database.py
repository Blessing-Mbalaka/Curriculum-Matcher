from django.conf import settings
from django.core.management.base import BaseCommand

from analysis.verification import verify_database


class Command(BaseCommand):
    help = "Verify stored skill extraction and SkillMatrix coverage with an Ollama LLM layer."

    def add_arguments(self, parser):
        parser.add_argument("--max-jobs", type=int, default=20)
        parser.add_argument("--max-modules", type=int, default=20)
        parser.add_argument("--min-text-chars", type=int, default=260)
        parser.add_argument("--suspicious-skill-count", type=int, default=1)
        parser.add_argument("--model", default=getattr(settings, "OLLAMA_VERIFICATION_MODEL", "ministral-3:3b"))
        parser.add_argument("--output-dir", default=None)
        parser.add_argument("--no-llm", action="store_true", help="Run heuristic verification without calling Ollama.")
        parser.add_argument(
            "--save-candidates",
            action="store_true",
            help="Save Ollama suggested missing skills as candidate rows for human review.",
        )

    def handle(self, *args, **options):
        result = verify_database(
            max_jobs=options["max_jobs"],
            max_modules=options["max_modules"],
            min_text_chars=options["min_text_chars"],
            suspicious_skill_count=options["suspicious_skill_count"],
            use_llm=not options["no_llm"],
            model=options["model"],
            output_dir=options["output_dir"],
            save_candidates=options["save_candidates"],
            progress_callback=self.stdout.write,
        )
        self.stdout.write(self.style.SUCCESS(
            "Verification complete: "
            f"{result['summary']['suspicious_records']} suspicious records, "
            f"{result['summary']['matrix_flags']} matrix flags."
        ))
        if options["save_candidates"]:
            self.stdout.write(f"Candidate skills saved: {result['summary'].get('candidate_skills_saved', 0)}")
        self.stdout.write(f"JSON: {result['paths']['json']}")
        self.stdout.write(f"Markdown: {result['paths']['markdown']}")
