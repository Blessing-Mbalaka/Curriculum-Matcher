import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.utils import timezone

from analysis.models import AnalysisRun, SkillMatrix
from analysis.spacyskillextraction import classify_skill_text
from courses.models import Module
from jobs.models import JobAdvert


REPORT_DIR = Path(settings.BASE_DIR) / "memory" / "verification"


def verify_database(
    max_jobs=20,
    max_modules=20,
    min_text_chars=260,
    suspicious_skill_count=1,
    use_llm=True,
    model=None,
    output_dir=None,
    save_candidates=False,
    progress_callback=None,
):
    """Verify extracted skill coverage against DB text evidence.

    The verifier is intentionally read-only. It reports suspicious records and
    LLM-suggested missing skills so a human can audit/reclassify before any
    extraction changes are made.
    """

    output_dir = Path(output_dir or REPORT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    model = model or getattr(settings, "OLLAMA_VERIFICATION_MODEL", "ministral-3:3b")

    def report(message):
        if progress_callback:
            progress_callback(message)

    report("Collecting suspicious module and job records...")
    modules = suspicious_module_records(
        limit=max_modules,
        min_text_chars=min_text_chars,
        suspicious_skill_count=suspicious_skill_count,
    )
    jobs = suspicious_job_records(
        limit=max_jobs,
        min_text_chars=min_text_chars,
        suspicious_skill_count=suspicious_skill_count,
    )
    matrix = skill_matrix_health()

    llm_available = False
    if use_llm and (modules or jobs):
        report(f"Verifying {len(modules)} modules and {len(jobs)} jobs with Ollama model {model}...")
        llm_available = True
        for record in modules + jobs:
            try:
                record["llm_verification"] = verify_record_with_ollama(record, model=model)
            except Exception as exc:
                llm_available = False
                record["llm_verification"] = {
                    "status": "error",
                    "reason": str(exc),
                    "suggested_skills": [],
                    "suspicious": True,
                    "notes": "LLM verification failed; inspect heuristic warnings.",
                }
    else:
        report("LLM verification disabled or no suspicious records found.")

    result = {
        "generated_at": timezone.now().isoformat(),
        "model": model if use_llm else "",
        "llm_available": llm_available,
        "settings": {
            "max_jobs": max_jobs,
            "max_modules": max_modules,
            "min_text_chars": min_text_chars,
            "suspicious_skill_count": suspicious_skill_count,
        },
        "summary": {
            "modules_checked": len(modules),
            "jobs_checked": len(jobs),
            "suspicious_records": len(modules) + len(jobs),
            "matrix_flags": len(matrix["flags"]),
        },
        "skill_matrix": matrix,
        "modules": modules,
        "jobs": jobs,
    }
    if save_candidates:
        saved = save_candidate_skill_entities(modules + jobs)
        result["summary"]["candidate_skills_saved"] = saved
        report(f"Saved {saved} candidate skill(s) for human review.")

    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"skill-verification-{stamp}.json"
    md_path = output_dir / f"skill-verification-{stamp}.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown_report(result), encoding="utf-8")
    result["paths"] = {"json": str(json_path), "markdown": str(md_path)}
    report(f"Wrote verification reports to {output_dir}.")
    return result


def save_candidate_skill_entities(records):
    saved = 0
    for record in records:
        verification = record.get("llm_verification") or {}
        if verification.get("status") != "needs_review":
            continue
        suggestions = verification.get("suggested_skills") or []
        if not suggestions:
            continue
        obj = source_object_for_record(record)
        if not obj:
            continue
        text = source_text_for_object(obj)
        entities = list(getattr(obj, "skill_entities", None) or [])
        if not entities and getattr(obj, "skills_extracted", None):
            entities = [
                build_legacy_entity(record, skill)
                for skill in (getattr(obj, "skills_extracted", None) or [])
                if normalize_skill(skill)
            ]
        existing = {
            normalize_skill(entity.get("skill") or entity.get("text"))
            for entity in entities
            if isinstance(entity, dict)
        }
        existing.update(normalized_skill_names(getattr(obj, "skills_extracted", None) or []))
        changed = False
        for skill in suggestions:
            skill = normalize_skill(skill)
            if not skill or skill in existing:
                continue
            entity = build_candidate_entity(record, skill, text)
            entities.append(entity)
            existing.add(skill)
            changed = True
            saved += 1
        if changed:
            obj.skill_entities = entities
            obj.save(update_fields=["skill_entities"])
    return saved


def source_object_for_record(record):
    source_type = record.get("source_type")
    source_id = record.get("source_id")
    if source_type == "module":
        return Module.objects.filter(pk=source_id).first()
    if source_type == "job":
        return JobAdvert.objects.filter(pk=source_id).first()
    return None


def source_text_for_object(obj):
    if isinstance(obj, Module):
        return obj.content or ""
    if isinstance(obj, JobAdvert):
        return obj.analysis_text()
    return ""


def build_candidate_entity(record, skill, text):
    classification = classify_skill_text(skill)
    start, end = find_skill_offsets(text, skill)
    source_type = record.get("source_type") or "source"
    source_id = record.get("source_id") or "0"
    entity_id = stable_skill_id(skill)
    return {
        "id": entity_id,
        "chunk_id": f"{source_type}-{source_id}-candidate-{entity_id}",
        "skill": skill,
        "label": "SKILL",
        "tier": "candidate",
        "skill_type": classification["skill_type"],
        "classification_scores": classification["scores"],
        "source": "ollama_verification",
        "confidence": 0.55,
        "mention_count": 1,
        "text": skill,
        "start": start,
        "end": end,
        "label_status": "candidate",
        "verification_model": record.get("llm_verification", {}).get("model") or "",
        "verification_notes": (record.get("llm_verification") or {}).get("notes", ""),
    }


def build_legacy_entity(record, skill):
    skill = normalize_skill(skill)
    classification = classify_skill_text(skill)
    source_type = record.get("source_type") or "source"
    source_id = record.get("source_id") or "0"
    entity_id = stable_skill_id(skill)
    return {
        "id": entity_id,
        "chunk_id": f"{source_type}-{source_id}-{entity_id}",
        "skill": skill,
        "label": "SKILL",
        "tier": classification["tier"],
        "skill_type": classification["skill_type"],
        "classification_scores": classification["scores"],
        "source": "legacy",
        "confidence": None,
        "mention_count": 1,
        "label_status": "legacy",
    }


def stable_skill_id(skill):
    canonical = normalize_skill(skill)
    slug = "".join(ch if ch.isalnum() else "-" for ch in canonical).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]
    return f"skill-{slug[:72]}" if slug else f"skill-{digest}"


def find_skill_offsets(text, skill):
    if not text or not skill:
        return None, None
    pattern = r"(?<!\w)" + re.escape(skill).replace(r"\ ", r"\s+") + r"(?!\w)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None, None
    return match.start(), match.end()


def suspicious_module_records(limit=20, min_text_chars=260, suspicious_skill_count=1):
    if limit <= 0:
        return []
    rows = []
    queryset = Module.objects.select_related("course").exclude(content="").order_by("course__code", "order", "name", "id")
    for module in queryset.iterator(chunk_size=100):
        text = module.content or ""
        skills = normalized_skill_names(module.skills_extracted or module.skill_entities or [])
        warnings = heuristic_warnings(text, skills, min_text_chars, suspicious_skill_count)
        if not warnings:
            continue
        rows.append({
            "source_type": "module",
            "source_id": module.id,
            "title": module.name,
            "parent": module.course.code or module.course.name,
            "text_chars": len(text),
            "skill_count": len(skills),
            "stored_skills": skills,
            "warnings": warnings,
            "text_sample": compact_text(text, 1800),
        })
        if len(rows) >= limit:
            break
    return rows


def suspicious_job_records(limit=20, min_text_chars=260, suspicious_skill_count=1):
    if limit <= 0:
        return []
    rows = []
    queryset = JobAdvert.objects.exclude(description="").order_by("-created_at", "title", "id")
    for job in queryset.iterator(chunk_size=100):
        text = job.analysis_text()
        skills = normalized_skill_names(job.skills_extracted or job.skill_entities or [])
        warnings = heuristic_warnings(text, skills, min_text_chars, suspicious_skill_count)
        if not warnings:
            continue
        rows.append({
            "source_type": "job",
            "source_id": job.id,
            "title": job.title,
            "parent": job.company or job.category or "",
            "text_chars": len(text),
            "skill_count": len(skills),
            "stored_skills": skills,
            "warnings": warnings,
            "text_sample": compact_text(text, 1800),
        })
        if len(rows) >= limit:
            break
    return rows


def heuristic_warnings(text, skills, min_text_chars, suspicious_skill_count):
    warnings = []
    if len(text or "") >= min_text_chars and len(skills) <= suspicious_skill_count:
        warnings.append(
            f"Long text has only {len(skills)} extracted skill(s); expected more evidence for the skill matrix."
        )
    if not skills:
        warnings.append("No extracted skills stored.")
    if len(set(skills)) != len(skills):
        warnings.append("Duplicate skill labels found after normalization.")
    return warnings


def skill_matrix_health():
    latest_run = AnalysisRun.objects.filter(skill_matrices__isnull=False).distinct().order_by("-created_at").first()
    if not latest_run:
        return {
            "run_id": None,
            "run_name": "",
            "flags": ["No SkillMatrix rows found for any analysis run."],
            "sources": {},
        }
    sources = {}
    flags = []
    for source in ("jobs", "courses"):
        rows = list(SkillMatrix.objects.filter(run=latest_run, source=source).order_by("-frequency")[:20])
        unique_count = SkillMatrix.objects.filter(run=latest_run, source=source).count()
        total_frequency = sum(row.frequency for row in rows)
        sources[source] = {
            "unique_skill_count": unique_count,
            "top_skills": [{"skill": row.skill, "frequency": row.frequency} for row in rows],
            "top_20_frequency": total_frequency,
        }
        if unique_count <= 1:
            flags.append(f"Latest {source} skill matrix has only {unique_count} unique skill(s).")
    return {
        "run_id": latest_run.id,
        "run_name": latest_run.name,
        "flags": flags,
        "sources": sources,
    }


def verify_record_with_ollama(record, model=None):
    model = model or getattr(settings, "OLLAMA_VERIFICATION_MODEL", "ministral-3:3b")
    endpoint = getattr(settings, "OLLAMA_VERIFICATION_ENDPOINT", "http://127.0.0.1:11434/api/generate")
    timeout = getattr(settings, "OLLAMA_VERIFICATION_TIMEOUT_SECONDS", 90)
    prompt = verification_prompt(record)
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "top_p": 0.7},
    }).encode("utf-8")
    req = urlrequest.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc
    content = raw.get("response") if isinstance(raw, dict) else ""
    if not content:
        raise RuntimeError("Ollama returned an empty response.")
    try:
        parsed = json.loads(extract_json_text(content))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON: {content[:300]}") from exc
    return normalize_verification_response(parsed)


def verification_prompt(record):
    max_chars = getattr(settings, "OLLAMA_VERIFICATION_PROMPT_MAX_CHARS", 9000)
    text = compact_text(record.get("text_sample", ""), max_chars)
    stored = ", ".join(record.get("stored_skills") or []) or "None"
    return f"""
You are a strict skill extraction verification layer for CurriculumMatch.
Your task is to check whether the stored extracted skills look incomplete for the source text.

Return valid JSON only:
{{
  "status": "ok|needs_review",
  "suspicious": true,
  "suggested_skills": ["short normalized skill names"],
  "false_positive_stored_skills": ["stored terms that are not skills"],
  "notes": "one concise explanation"
}}

Rules:
- Suggest concrete skills explicitly supported by the text.
- Do not invent skills from the title alone.
- Use short normalized lowercase labels.
- Keep suggested_skills to at most 12 items.
- If the stored skills are sufficient, return status "ok" and suspicious false.

Source type: {record.get("source_type")}
Title: {record.get("title")}
Parent: {record.get("parent")}
Stored skills: {stored}
Warnings: {"; ".join(record.get("warnings") or [])}

Text:
{text}
""".strip()


def normalize_verification_response(response):
    if not isinstance(response, dict):
        response = {}
    suggested = normalize_llm_skill_list(response.get("suggested_skills"))
    false_positive = normalize_llm_skill_list(response.get("false_positive_stored_skills"))
    status = str(response.get("status") or "").lower()
    if status not in {"ok", "needs_review"}:
        status = "needs_review" if suggested or false_positive else "ok"
    return {
        "status": status,
        "suspicious": bool(response.get("suspicious", status == "needs_review")),
        "suggested_skills": suggested[:12],
        "false_positive_stored_skills": false_positive[:12],
        "notes": compact_text(response.get("notes", ""), 700),
    }


def normalized_skill_names(raw_items):
    names = []
    seen = set()
    for raw in raw_items or []:
        if isinstance(raw, dict):
            skill = raw.get("skill") or raw.get("text") or ""
        else:
            skill = str(raw or "")
        skill = normalize_skill(skill)
        if skill and skill not in seen:
            seen.add(skill)
            names.append(skill)
    return names


def normalize_llm_skill_list(value):
    if isinstance(value, str):
        value = re.split(r"[,;\n]", value)
    if not isinstance(value, list):
        return []
    names = []
    seen = set()
    for item in value:
        skill = normalize_skill(item)
        if skill and skill not in seen:
            seen.add(skill)
            names.append(skill)
    return names


def normalize_skill(value):
    return " ".join(str(value or "").lower().replace("-", " ").replace("_", " ").split())


def extract_json_text(value):
    text = str(value or "").strip()
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        return text[first:last + 1]
    return text


def compact_text(value, max_chars=1800):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].strip() + "..."


def render_markdown_report(result):
    lines = [
        "# Skill Extraction Verification",
        "",
        f"Generated: {result['generated_at']}",
        f"Ollama model: {result.get('model') or 'disabled'}",
        f"LLM available: {result.get('llm_available')}",
        "",
        "## Summary",
        "",
        f"- Suspicious modules checked: {result['summary']['modules_checked']}",
        f"- Suspicious jobs checked: {result['summary']['jobs_checked']}",
        f"- Skill matrix flags: {result['summary']['matrix_flags']}",
        "",
        "## Skill Matrix",
        "",
    ]
    matrix = result.get("skill_matrix") or {}
    lines.append(f"Latest run: {matrix.get('run_name') or 'None'}")
    for flag in matrix.get("flags") or []:
        lines.append(f"- {flag}")
    for source, details in (matrix.get("sources") or {}).items():
        lines.extend(["", f"### {source.title()}", ""])
        lines.append(f"Unique skills: {details.get('unique_skill_count', 0)}")
        for row in details.get("top_skills") or []:
            lines.append(f"- {row['skill']}: {row['frequency']}")
    lines.extend(["", "## Records Needing Review", ""])
    for section in ("modules", "jobs"):
        lines.extend(["", f"### {section.title()}", ""])
        records = result.get(section) or []
        if not records:
            lines.append("No suspicious records found.")
            continue
        for record in records:
            verification = record.get("llm_verification") or {}
            suggestions = ", ".join(verification.get("suggested_skills") or []) or "None"
            false_positive = ", ".join(verification.get("false_positive_stored_skills") or []) or "None"
            lines.extend([
                f"#### {record['source_type']} {record['source_id']}: {record['title']}",
                "",
                f"- Parent: {record.get('parent') or '-'}",
                f"- Stored skills ({record['skill_count']}): {', '.join(record.get('stored_skills') or []) or 'None'}",
                f"- Warnings: {'; '.join(record.get('warnings') or [])}",
                f"- LLM status: {verification.get('status', 'not_run')}",
                f"- Suggested missing skills: {suggestions}",
                f"- Possible false positives: {false_positive}",
                f"- Notes: {verification.get('notes') or '-'}",
                "",
            ])
    return "\n".join(lines).strip() + "\n"
