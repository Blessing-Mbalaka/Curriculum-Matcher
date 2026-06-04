"""
Core gap analysis orchestrator.
"""

import logging

from courses.models import Course, Module
from jobs.models import JobAdvert
from .models import AnalysisRun, GapResult, SkillMatrix
from .nlp_pipeline import compute_gap
from .semantic_similarity import SemanticSimilarityService
from .spacyskillextraction import SpacySkillExtractor

logger = logging.getLogger(__name__)


def run_gap_analysis(run_name: str = "Analysis Run", progress_callback=None, max_jobs=None) -> AnalysisRun:
    def report(percent: int, message: str) -> None:
        if progress_callback:
            progress_callback(percent, message)

    def should_report(index: int, total: int) -> bool:
        return total <= 10 or index == total or index % max(1, total // 50) == 0

    report(2, "Creating analysis run...")
    run = AnalysisRun.objects.create(name=run_name, status="running")

    try:
        report(5, "Loading courses and job adverts...")
        courses = list(Course.objects.prefetch_related("modules").all())
        jobs_queryset = JobAdvert.objects.all()
        if max_jobs:
            jobs_queryset = jobs_queryset[:max(1, int(max_jobs))]
        jobs = list(jobs_queryset)

        if not courses:
            raise ValueError("No courses found. Add at least one course with modules first.")
        if not jobs:
            raise ValueError("No job adverts found. Import jobs before running analysis.")

        # Build corpus
        module_map = {}   # module_id -> content
        for c in courses:
            for m in c.modules.all():
                if m.content.strip():
                    module_map[m.id] = m.content

        job_map = {j.id: j.analysis_text() for j in jobs if j.analysis_text().strip()}

        all_texts = list(module_map.values()) + list(job_map.values())
        logger.info("Preparing semantic scorer on %s documents...", len(all_texts))
        limit_note = f" Smoke limit: {len(jobs)} job adverts." if max_jobs else ""
        report(12, f"Preparing semantic scorer on {len(all_texts)} documents...{limit_note}")
        scorer = SemanticSimilarityService(
            all_texts,
            progress_callback=lambda message: report(14, message),
        )
        report(18, f"Semantic scorer ready using {scorer.backend}.")
        skill_extractor = SpacySkillExtractor()
        report(22, f"Skill extractor ready using {skill_extractor.backend}.")
        report(25, "Extracting module skills...")

        # Vectorise modules
        module_data = {}
        module_items = list(module_map.items())
        for index, (mid, text) in enumerate(module_items, start=1):
            skill_entities = skill_extractor.extract_entities(text, document_id=f"module-{mid}")
            module_data[mid] = {
                "vector": scorer.vectorize(text),
                "skills": sorted({entity["skill"] for entity in skill_entities}),
                "skill_entities": skill_entities,
            }
            Module.objects.filter(id=mid).update(
                vector=module_data[mid]["vector"].tolist(),
                skills_extracted=module_data[mid]["skills"],
                skill_entities=module_data[mid]["skill_entities"],
            )
            if should_report(index, len(module_items)):
                report(25 + int(20 * index / max(1, len(module_items))), f"Processed {index}/{len(module_items)} modules...")

        # Vectorise jobs
        report(45, "Extracting job advert skills...")
        job_data = {}
        job_items = list(job_map.items())
        for index, (jid, text) in enumerate(job_items, start=1):
            skill_entities = skill_extractor.extract_entities(text, document_id=f"job-{jid}")
            job_data[jid] = {
                "vector": scorer.vectorize(text),
                "skills": sorted({entity["skill"] for entity in skill_entities}),
                "skill_entities": skill_entities,
            }
            JobAdvert.objects.filter(id=jid).update(
                vector=job_data[jid]["vector"].tolist(),
                skills_extracted=job_data[jid]["skills"],
                skill_entities=job_data[jid]["skill_entities"],
            )
            if should_report(index, len(job_items)):
                report(45 + int(20 * index / max(1, len(job_items))), f"Processed {index}/{len(job_items)} job adverts...")

        # Score each course vs each job
        report(65, "Scoring course and job matches...")
        gap_results = []
        total_courses = len(courses)
        for course_index, course in enumerate(courses, start=1):
            mids = [m.id for m in course.modules.all() if m.id in module_data]
            if not mids:
                continue

            course_vectors = [module_data[mid]["vector"] for mid in mids]
            course_skills = list({s for mid in mids for s in module_data[mid]["skills"]})

            for job in jobs:
                if job.id not in job_data:
                    continue
                matched, missing, extra = compute_gap(course_skills, job_data[job.id]["skills"])
                semantic_score = scorer.course_job_semantic_score(course_vectors, job_data[job.id]["vector"])
                score = scorer.final_score(semantic_score, matched, job_data[job.id]["skills"]).final_score
                gap_results.append(GapResult(
                    run=run, course=course, job=job,
                    similarity_score=score,
                    matched_skills=matched,
                    missing_skills=missing,
                    extra_skills=extra,
                ))
            if should_report(course_index, total_courses):
                report(65 + int(20 * course_index / max(1, total_courses)), f"Scored {course_index}/{total_courses} courses...")

        # Bulk insert gap results in chunks
        report(85, f"Saving {len(gap_results)} match results...")
        chunk = 500
        for i in range(0, len(gap_results), chunk):
            GapResult.objects.bulk_create(gap_results[i:i+chunk], ignore_conflicts=True)

        # Skill matrices
        report(92, "Building skill matrices...")
        SkillMatrix.objects.bulk_create([
            SkillMatrix(run=run, source="jobs", skill=s, frequency=f)
            for s, f in skill_extractor.build_skill_matrix(list(job_map.values()))
        ], ignore_conflicts=True)
        SkillMatrix.objects.bulk_create([
            SkillMatrix(run=run, source="courses", skill=s, frequency=f)
            for s, f in skill_extractor.build_skill_matrix(list(module_map.values()))
        ], ignore_conflicts=True)

        run.status = "done"
        run.save()
        report(100, f"Analysis complete. Created {len(gap_results)} results.")
        logger.info(f"Analysis '{run.name}' complete — {len(gap_results)} results.")

    except Exception as exc:
        run.status = "error"
        run.notes = str(exc)
        run.save()
        logger.error(f"Analysis failed: {exc}")
        raise

    return run
