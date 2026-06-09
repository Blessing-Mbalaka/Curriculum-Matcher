import json
import re

import requests
from django.conf import settings


GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

JOB_CLEANING_SCHEMA = {
    "clean_title": "",
    "seniority": "",
    "sector": "",
    "occupation_family": "",
    "required_skills": [],
    "preferred_skills": [],
    "tools": [],
    "soft_skills": [],
    "experience_years_min": None,
    "education_level": "",
    "work_mode": "",
    "contract_type": "",
    "salary_min": None,
    "salary_max": None,
    "data_quality_flags": [],
}


class GeminiCleaningError(Exception):
    pass


def gemini_is_configured():
    return bool(getattr(settings, "GEMINI_API_KEY", ""))


def clean_job_advert_with_gemini(job, compact_text=""):
    if not gemini_is_configured():
        raise GeminiCleaningError("Missing GEMINI_API_KEY.")

    prompt = _job_cleaning_prompt(job, compact_text=compact_text)
    url = GEMINI_ENDPOINT.format(model=getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash"))
    response = requests.post(
        url,
        headers={
            "x-goog-api-key": settings.GEMINI_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        },
        timeout=45,
    )
    if response.status_code >= 400:
        raise GeminiCleaningError(f"Gemini request failed with HTTP {response.status_code}: {response.text[:300]}")

    text = _response_text(response.json())
    payload = _parse_json_payload(text)
    return normalize_cleaned_payload(payload)


def normalize_cleaned_payload(payload):
    cleaned = dict(JOB_CLEANING_SCHEMA)
    if isinstance(payload, dict):
        cleaned.update({key: payload.get(key) for key in cleaned if key in payload})

    for key in ("required_skills", "preferred_skills", "tools", "soft_skills", "data_quality_flags"):
        value = cleaned.get(key)
        if isinstance(value, str):
            value = [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]
        if not isinstance(value, list):
            value = []
        cleaned[key] = _unique_strings(value)

    for key in ("salary_min", "salary_max", "experience_years_min"):
        cleaned[key] = _nullable_int(cleaned.get(key))

    for key, value in list(cleaned.items()):
        if value is None or isinstance(value, list):
            continue
        cleaned[key] = " ".join(str(value).strip().split())

    return cleaned


def _job_cleaning_prompt(job, compact_text=""):
    source_text = _limit_prompt_text(compact_text.strip() or _full_job_text(job))
    return f"""
You clean job advert data for curriculum-to-labour-market analytics.
Return only valid JSON with these exact keys:
{json.dumps(JOB_CLEANING_SCHEMA, indent=2)}

Rules:
- Extract skills from the supplied job evidence, not only the title.
- required_skills are mandatory skills or capabilities.
- preferred_skills are nice-to-have skills.
- tools are named software, platforms, languages, frameworks, methods, or systems.
- soft_skills are interpersonal or transferable capabilities.
- data_quality_flags should include short labels for issues such as missing_salary, vague_description, missing_location, duplicate_like, or noisy_html.
- Use null for unknown numeric fields.
- Do not invent facts that are not supported by the advert.

Job evidence:
{source_text}
""".strip()


def _limit_prompt_text(text):
    max_chars = max(4000, int(getattr(settings, "GEMINI_PROMPT_MAX_CHARS", 24000)))
    if len(text or "") <= max_chars:
        return text or ""

    head_chars = int(max_chars * 0.70)
    tail_chars = max_chars - head_chars
    omitted = len(text) - max_chars
    return (
        text[:head_chars].rstrip()
        + f"\n\n[... {omitted} characters omitted to stay inside the model context window ...]\n\n"
        + text[-tail_chars:].lstrip()
    )


def _full_job_text(job):
    return f"""
Title: {job.title}
Company: {job.company}
Recruiter: {job.recruiter}
Location: {job.location}
Category: {job.category}
Contract type: {job.contract_type}
Contract time: {job.contract_time}
Salary min: {job.salary_min}
Salary max: {job.salary_max}
Summary:
{job.summary}

Position information:
{job.position_info}

Description:
{job.raw_description or job.description}
""".strip()


def _response_text(data):
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiCleaningError("Gemini returned an unexpected response shape.") from exc
    return "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()


def _parse_json_payload(text):
    if not text:
        raise GeminiCleaningError("Gemini returned an empty response.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise GeminiCleaningError("Gemini response did not contain JSON.")
        return json.loads(match.group(0))


def _unique_strings(values):
    seen = set()
    result = []
    for value in values:
        item = " ".join(str(value).strip().split())
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _nullable_int(value):
    if value in ("", None):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
