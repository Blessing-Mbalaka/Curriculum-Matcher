from django.core.management.base import BaseCommand
from django.test import RequestFactory

from dashboard.cache import clear_dashboard_cache, load_or_build_json_cache
from dashboard.views import (
    build_dashboard_metrics_payload,
    build_data_export_bundle,
    build_similarity_network_payload,
    build_skill_forecast_payload,
    build_skill_vector_space_payload,
)


class Command(BaseCommand):
    help = "Warm persistent dashboard chart caches so key pages load from prebuilt payloads."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true", help="Rebuild cached payloads even if they already exist.")
        parser.add_argument("--clear", action="store_true", help="Delete existing dashboard cache files before warming.")

    def handle(self, *args, **options):
        refresh = bool(options["refresh"])
        if options["clear"]:
            deleted = clear_dashboard_cache()
            self.stdout.write(f"Cleared {deleted} cached dashboard payload(s).")

        factory = RequestFactory()
        warm_specs = [
            ("dashboard_metrics", {}, build_dashboard_metrics_payload),
            ("similarity_network", {}, build_similarity_network_payload),
            ("similarity_network", {"cluster": "1"}, build_similarity_network_payload),
            ("data_export_bundle", {}, build_data_export_bundle),
            ("data_export_bundle", {"source": "job"}, build_data_export_bundle),
            ("data_export_bundle", {"source": "module"}, build_data_export_bundle),
            ("data_export_forecast", {}, build_skill_forecast_payload),
            ("data_export_forecast", {"source": "job"}, build_skill_forecast_payload),
            ("data_export_forecast", {"source": "module"}, build_skill_forecast_payload),
            ("skill_vector_space", {}, build_skill_vector_space_payload),
            ("skill_vector_space", {"source": "job"}, build_skill_vector_space_payload),
            ("skill_vector_space", {"source": "module"}, build_skill_vector_space_payload),
        ]

        warmed = 0
        hits = 0
        for scope, params, builder in warm_specs:
            request = factory.get("/", data=params)
            payload, cache_hit = load_or_build_json_cache(
                scope,
                request.GET,
                lambda builder=builder, request=request: builder(request),
                refresh=refresh,
            )
            warmed += 1
            hits += 1 if cache_hit else 0
            self.stdout.write(
                f"[{'hit' if cache_hit else 'built'}] {scope} {dict(request.GET)}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Dashboard cache warm complete: {warmed} payload(s), {hits} cache hit(s), {warmed - hits} rebuilt."
            )
        )
