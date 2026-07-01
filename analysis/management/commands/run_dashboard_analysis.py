from collections import Counter
from django.db.models import Count

from django.core.management.base import BaseCommand
from django.utils import timezone

from analysis.course_skill_ner import ensure_course_skill_ner_model
from analysis.models import AnalysisRun, GapResult, SkillMatrix, TaskRecord
from analysis.nlp_pipeline import compute_gap
from analysis.semantic_similarity import SemanticSimilarityService
from analysis.services import _matched_skill_confidence
from analysis.spacyskillextraction import SpacySkillExtractor
from courses.models import Course, Module
from jobs.models import JobAdvert


class Command(BaseCommand):
    help = "Run a standalone dashboard analysis against the current database contents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--run-name",
            default="Dashboard Analysis Refresh",
            help="Analysis run name to create.",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=None,
            help="Optional cap for the number of jobs scored.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=None,
            help="Process jobs in batches until the whole selected set is finished.",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Resume the latest unfinished chunked run with the same run name.",
        )
        parser.add_argument(
            "--resume-run-id",
            type=int,
            default=None,
            help="Resume a specific unfinished AnalysisRun id.",
        )

    def handle(self, *args, **options):
        task = TaskRecord.objects.create(
            task_id=f"dashboard-analysis:{timezone.now():%Y%m%d%H%M%S}",
            run_name=options["run_name"],
            status="STARTED",
            progress=1,
            notes="Preparing dashboard analysis...",
        )

        try:
            if options["chunk_size"]:
                run = self._run_chunked(task, options)
            else:
                run = self._run_simple(task, options)
            TaskRecord.objects.filter(id=task.id).update(
                status="SUCCESS",
                progress=100,
                notes=f"Analysis run #{run.id} completed with status {run.status}.",
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Analysis run #{run.id} completed with status {run.status}."
                )
            )
        except Exception as exc:
            TaskRecord.objects.filter(id=task.id).update(
                status="FAILURE",
                notes=str(exc),
                finished_at=timezone.now(),
                updated_at=timezone.now(),
            )
            raise

    def _report(self, task_id, percent, message):
        TaskRecord.objects.filter(id=task_id).update(
            status="STARTED",
            progress=max(1, min(100, int(percent))),
            notes=message,
            updated_at=timezone.now(),
        )
        self.stdout.write(message)

    def _run_simple(self, task, options):
        from analysis.services import run_gap_analysis

        def report(percent, message):
            self._report(task.id, percent, message)

        return run_gap_analysis(
            run_name=options["run_name"],
            progress_callback=report,
            max_jobs=options["max_jobs"],
        )

    def _run_chunked(self, task, options):
        chunk_size = max(1, int(options["chunk_size"]))
        courses = list(Course.objects.prefetch_related("modules").all())
        if not courses:
            raise ValueError("No courses found. Add at least one course with modules first.")

        job_ids = list(JobAdvert.objects.order_by("id").values_list("id", flat=True))
        if options["max_jobs"]:
            job_ids = job_ids[:max(1, int(options["max_jobs"]))]
        if not job_ids:
            raise ValueError("No job adverts found. Import jobs before running analysis.")

        run = self._resolve_run(options)
        self._report(task.id, 3, f"Using analysis run #{run.id}.")

        completed_job_ids = self._completed_job_ids(run, len(courses))
        remaining_job_ids = [job_id for job_id in job_ids if job_id not in completed_job_ids]
        completed_before = len(job_ids) - len(remaining_job_ids)
        self._report(
            task.id,
            5,
            f"Run #{run.id} already has complete results for {completed_before}/{len(job_ids)} jobs.",
        )

        module_map = {}
        for course in courses:
            for module in course.modules.all():
                if module.content.strip():
                    module_map[module.id] = module.content
        if not module_map:
            raise ValueError("No course module content found to analyze.")

        initial_seed_job_ids = remaining_job_ids[: min(len(remaining_job_ids), chunk_size)] or job_ids[: min(len(job_ids), chunk_size)]
        initial_jobs = list(JobAdvert.objects.filter(id__in=initial_seed_job_ids).order_by("id"))
        initial_texts = [job.analysis_text() for job in initial_jobs if job.analysis_text().strip()]
        scorer = SemanticSimilarityService(
            list(module_map.values()) + initial_texts,
            progress_callback=lambda message: self._report(task.id, 8, message),
        )
        self._report(task.id, 10, f"Semantic scorer ready using {scorer.backend}.")

        ner_result = ensure_course_skill_ner_model(
            progress_callback=lambda message: self._report(task.id, 12, message),
        )
        if ner_result.get("trained"):
            self._report(task.id, 14, f"Course skill NER model trained using {ner_result['train_examples']} examples.")
        else:
            self._report(task.id, 14, f"Course skill NER training skipped: {ner_result.get('reason', 'not needed')}")

        extractor = SpacySkillExtractor()
        self._report(task.id, 16, f"Skill extractor ready using {extractor.backend}.")

        module_data = {}
        course_skill_counts = Counter()
        modules = list(Module.objects.filter(id__in=module_map).order_by("id"))
        for index, module in enumerate(modules, start=1):
            skill_entities = list(module.skill_entities or [])
            if not skill_entities:
                skill_entities = extractor.extract_entities(module.content, document_id=f"module-{module.id}")
            skills = sorted({entity["skill"] for entity in skill_entities if entity.get("skill")})
            vector = scorer.vectorize(module.content)
            module_data[module.id] = {
                "vector": vector,
                "skills": skills,
                "skill_entities": skill_entities,
            }
            course_skill_counts.update(skills)
            Module.objects.filter(id=module.id).update(
                vector=vector.tolist(),
                skills_extracted=skills,
                skill_entities=skill_entities,
            )
            self._report(task.id, 16 + int(8 * index / max(1, len(modules))), f"Prepared module {index}/{len(modules)}.")

        total_jobs = len(job_ids)
        processed_jobs = completed_before
        for batch_start in range(0, len(remaining_job_ids), chunk_size):
            batch_ids = remaining_job_ids[batch_start: batch_start + chunk_size]
            batch_jobs = list(JobAdvert.objects.filter(id__in=batch_ids).order_by("id"))
            job_data = {}

            for job in batch_jobs:
                text = job.analysis_text()
                if not text.strip():
                    continue
                skill_entities = list(job.skill_entities or [])
                if not skill_entities:
                    skill_entities = extractor.extract_entities(text, document_id=f"job-{job.id}")
                skills = sorted({entity["skill"] for entity in skill_entities if entity.get("skill")})
                vector = scorer.vectorize(text)
                job_data[job.id] = {
                    "vector": vector,
                    "skills": skills,
                    "skill_entities": skill_entities,
                }
                JobAdvert.objects.filter(id=job.id).update(
                    vector=vector.tolist(),
                    skills_extracted=skills,
                    skill_entities=skill_entities,
                )

            results = []
            for course in courses:
                module_ids = [module.id for module in course.modules.all() if module.id in module_data]
                if not module_ids:
                    continue
                course_vectors = [module_data[module_id]["vector"] for module_id in module_ids]
                course_skills = sorted({skill for module_id in module_ids for skill in module_data[module_id]["skills"]})
                course_skill_entities = [
                    entity
                    for module_id in module_ids
                    for entity in module_data[module_id]["skill_entities"]
                ]
                for job in batch_jobs:
                    if job.id not in job_data:
                        continue
                    matched, missing, extra = compute_gap(course_skills, job_data[job.id]["skills"])
                    semantic_score = scorer.course_job_semantic_score(course_vectors, job_data[job.id]["vector"])
                    confidence_score = _matched_skill_confidence(
                        matched,
                        course_skill_entities,
                        job_data[job.id]["skill_entities"],
                    )
                    score = scorer.final_score(
                        semantic_score,
                        matched,
                        job_data[job.id]["skills"],
                        confidence_score=confidence_score,
                    )
                    results.append(
                        GapResult(
                            run=run,
                            course=course,
                            job=job,
                            similarity_score=score.final_score,
                            score_breakdown=score.as_dict(),
                            matched_skills=matched,
                            missing_skills=missing,
                            extra_skills=extra,
                        )
                    )

            GapResult.objects.bulk_create(results, ignore_conflicts=True, batch_size=1000)
            processed_jobs += len(batch_jobs)
            percent = 25 + int(65 * processed_jobs / max(1, total_jobs))
            self._report(
                task.id,
                percent,
                f"Processed jobs {processed_jobs}/{total_jobs} in chunks of {chunk_size}.",
            )

        job_skill_counts = self._job_skill_counts(job_ids)
        self._finalize_run(run, scorer, course_skill_counts, job_skill_counts)
        self._report(task.id, 98, f"Chunked analysis complete for run #{run.id}.")
        return run

    def _resolve_run(self, options):
        run_id = options.get("resume_run_id")
        resume = options.get("resume")
        run_name = options["run_name"]

        if run_id:
            run = AnalysisRun.objects.filter(id=run_id).first()
            if not run:
                raise ValueError(f"AnalysisRun #{run_id} was not found.")
            if run.status == "done":
                raise ValueError(f"AnalysisRun #{run_id} is already done.")
            run.status = "running"
            run.save(update_fields=["status"])
            return run

        if resume:
            run = AnalysisRun.objects.filter(name=run_name).exclude(status="done").order_by("-created_at").first()
            if run:
                run.status = "running"
                run.save(update_fields=["status"])
                return run

        return AnalysisRun.objects.create(name=run_name, status="running")

    def _completed_job_ids(self, run, course_count):
        return set(
            GapResult.objects.filter(run=run)
            .values("job_id")
            .annotate(total=Count("id"))
            .filter(total__gte=course_count)
            .values_list("job_id", flat=True)
        )

    def _job_skill_counts(self, job_ids):
        counter = Counter()
        for job in JobAdvert.objects.filter(id__in=job_ids).only("skills_extracted").iterator(chunk_size=1000):
            counter.update(skill for skill in (job.skills_extracted or []) if skill)
        return counter

    def _finalize_run(self, run, scorer, course_skill_counts, job_skill_counts):
        SkillMatrix.objects.filter(run=run).delete()
        SkillMatrix.objects.bulk_create(
            [
                SkillMatrix(run=run, source="jobs", skill=skill, frequency=frequency)
                for skill, frequency in job_skill_counts.items()
            ],
            batch_size=1000,
        )
        SkillMatrix.objects.bulk_create(
            [
                SkillMatrix(run=run, source="courses", skill=skill, frequency=frequency)
                for skill, frequency in course_skill_counts.items()
            ],
            batch_size=1000,
        )

        run.status = "done"
        if getattr(scorer, "embedding_failures", 0):
            run.notes = (
                f"Chunked analysis completed with {scorer.embedding_failures} semantic embedding "
                "fallback(s). Affected records used neutral semantic vectors."
            )
        run.save(update_fields=["status", "notes"])
