"""
Core gap analysis orchestrator.
"""

import logging
import numpy as np

from courses.models import Course, Module
from jobs.models import JobAdvert
from .models import AnalysisRun, GapResult, SkillMatrix
from .nlp_pipeline import (
    train_word2vec, document_vector, compute_similarity,
    compute_gap, extract_skills, build_skill_matrix,
)

logger = logging.getLogger(__name__)


def run_gap_analysis(run_name: str = "Analysis Run", progress_callback=None) -> AnalysisRun:
    def report(percent: int, message: str) -> None:
        if progress_callback:
            progress_callback(percent, message)

    def should_report(index: int, total: int) -> bool:
        return total <= 10 or index == total or index % max(1, total // 20) == 0

    report(2, "Creating analysis run...")
    run = AnalysisRun.objects.create(name=run_name, status="running")

    try:
        report(5, "Loading courses and job adverts...")
        courses = list(Course.objects.prefetch_related("modules").all())
        jobs = list(JobAdvert.objects.all())

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

        job_map = {j.id: j.description for j in jobs if j.description.strip()}

        all_texts = list(module_map.values()) + list(job_map.values())
        logger.info(f"Training Word2Vec on {len(all_texts)} documents…")
        report(12, f"Training Word2Vec on {len(all_texts)} documents...")
        model = train_word2vec(all_texts)
        report(25, "Extracting module skills...")

        # Vectorise modules
        module_data = {}
        module_items = list(module_map.items())
        for index, (mid, text) in enumerate(module_items, start=1):
            module_data[mid] = {
                "vector": document_vector(model, text),
                "skills": extract_skills(text),
            }
            Module.objects.filter(id=mid).update(
                vector=module_data[mid]["vector"].tolist(),
                skills_extracted=module_data[mid]["skills"],
            )
            if should_report(index, len(module_items)):
                report(25 + int(20 * index / max(1, len(module_items))), f"Processed {index}/{len(module_items)} modules...")

        # Vectorise jobs
        report(45, "Extracting job advert skills...")
        job_data = {}
        job_items = list(job_map.items())
        for index, (jid, text) in enumerate(job_items, start=1):
            job_data[jid] = {
                "vector": document_vector(model, text),
                "skills": extract_skills(text),
            }
            JobAdvert.objects.filter(id=jid).update(
                vector=job_data[jid]["vector"].tolist(),
                skills_extracted=job_data[jid]["skills"],
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

            course_vec = np.mean([module_data[mid]["vector"] for mid in mids], axis=0)
            course_skills = list({s for mid in mids for s in module_data[mid]["skills"]})

            for job in jobs:
                if job.id not in job_data:
                    continue
                score = compute_similarity(course_vec, job_data[job.id]["vector"])
                matched, missing, extra = compute_gap(course_skills, job_data[job.id]["skills"])
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
            for s, f in build_skill_matrix(list(job_map.values()))
        ], ignore_conflicts=True)
        SkillMatrix.objects.bulk_create([
            SkillMatrix(run=run, source="courses", skill=s, frequency=f)
            for s, f in build_skill_matrix(list(module_map.values()))
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
