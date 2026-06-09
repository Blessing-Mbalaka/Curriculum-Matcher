import re
from pathlib import Path

from django.conf import settings


DEFAULT_MEMORY_PATH = Path(settings.BASE_DIR) / "memory" / "jobs_memory.md"


def build_job_memory_block(job, max_description_chars=1200):
    skills = _skill_names(job)
    description = _compact_text(job.raw_description or job.description, max_description_chars)
    salary = _salary_text(job)
    created = job.created_at.date().isoformat() if job.created_at else ""
    posted = job.date_posted.isoformat() if job.date_posted else ""

    lines = [
        f"## JOB {job.id}",
        "",
        f"Title: {_clean_line(job.title)}",
        f"Company: {_clean_line(job.company) or 'Unknown'}",
        f"Recruiter: {_clean_line(job.recruiter) or 'Unknown'}",
        f"Location: {_clean_line(job.location) or 'Unknown'}",
        f"Category: {_clean_line(job.category) or 'Unknown'}",
        f"Contract: {_clean_line(job.contract_type) or 'Unknown'} / {_clean_line(job.contract_time) or 'Unknown'}",
        f"Salary: {salary}",
        f"Source: {_clean_line(job.source)}",
        f"Date posted: {posted or 'Unknown'}",
        f"Date stored: {created or 'Unknown'}",
        "",
        "Summary:",
        _compact_text(job.summary, 500) or "Unknown",
        "",
        "Position information:",
        _compact_text(job.position_info, 700) or "Unknown",
        "",
        "Description evidence:",
        description or "Unknown",
        "",
        "Existing extracted skills:",
    ]
    if skills:
        lines.extend(f"- {skill}" for skill in skills[:40])
    else:
        lines.append("- Unknown")
    lines.append("")
    return "\n".join(lines)


def write_jobs_memory(jobs, output_path=DEFAULT_MEMORY_PATH, max_description_chars=1200):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        "# Job Advert LLM Memory",
        "",
        "This file is generated from the database. Use job IDs to write cleaned data back to JobAdvert records.",
        "",
    ]
    for job in jobs:
        blocks.append(build_job_memory_block(job, max_description_chars=max_description_chars))
    text = "\n".join(blocks).strip() + "\n"
    output_path.write_text(text, encoding="utf-8")
    return output_path, len(text)


def _skill_names(job):
    seen = set()
    skills = []
    raw_entities = job.skill_entities or job.skills_extracted or []
    for raw in raw_entities:
        if isinstance(raw, dict):
            skill = raw.get("skill") or raw.get("text") or ""
        else:
            skill = str(raw or "")
        skill = _clean_line(skill)
        key = skill.lower()
        if skill and key not in seen:
            seen.add(key)
            skills.append(skill)
    return skills


def _salary_text(job):
    if job.salary_min and job.salary_max:
        return f"{job.salary_min}-{job.salary_max}"
    if job.salary_min:
        return f"from {job.salary_min}"
    if job.salary_max:
        return f"up to {job.salary_max}"
    return "Unknown"


def _compact_text(value, max_chars):
    text = _clean_line(value)
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rsplit(" ", 1)[0].strip()
    return f"{trimmed}..."


def _clean_line(value):
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text
