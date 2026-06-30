import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check whether semantic embeddings are available and report which backend Django will use."

    def add_arguments(self, parser):
        parser.add_argument(
            "--require-embedding",
            action="store_true",
            help="Exit with a non-zero status if Django falls back to Word2Vec.",
        )

    def handle(self, *args, **options):
        ollama_host = os.environ.get("OLLAMA_HOST", "")
        self.stdout.write(f"OLLAMA_HOST={ollama_host or '<unset>'}")
        self.stdout.write(f"OLLAMA_EMBED_MODEL={getattr(settings, 'OLLAMA_EMBED_MODEL', 'nomic-embed-text')}")
        self.stdout.write(f"SEMANTIC_MODEL_NAME={getattr(settings, 'SEMANTIC_MODEL_NAME', 'sentence-transformers/all-MiniLM-L6-v2')}")

        try:
            from analysis.semantic_similarity import SemanticSimilarityService
        except ModuleNotFoundError as exc:
            raise CommandError(
                f"Missing Python dependency: {exc.name}. Install project requirements before checking embeddings."
            ) from exc

        service_messages = []
        service = SemanticSimilarityService(
            [
                "Business analytics, statistics, forecasting, and SQL for decision support.",
                "Strategy, leadership, stakeholder communication, and process improvement.",
            ],
            progress_callback=service_messages.append,
        )

        if service_messages:
            self.stdout.write("")
            self.stdout.write("Initialization log:")
            for message in service_messages:
                self.stdout.write(f"  - {message}")

        self.stdout.write("")
        self.stdout.write(f"Selected backend: {service.backend}")
        self.stdout.write(f"Embedding dimension: {service.embedding_dim or 'unknown'}")

        sample_vector = service.vectorize("Python, SQL, dashboards, and data storytelling.")
        self.stdout.write(f"Sample vector length: {len(sample_vector)}")
        self.stdout.write(f"Embedding failures during probe: {service.embedding_failures}")

        if options["require_embedding"] and service.backend == "word2vec":
            raise CommandError(
                "No embedding backend is available. Django fell back to Word2Vec. "
                "Check Ollama service/model availability or sentence-transformers model loading."
            )

        if service.backend == "word2vec":
            self.stdout.write(
                self.style.WARNING(
                    "Word2Vec fallback is active. Semantic embedding backends are not currently available."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("Embedding backend is available and working."))
