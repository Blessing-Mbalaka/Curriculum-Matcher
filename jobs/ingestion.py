"""
Job ingestion: CSV upload and Adzuna API.
"""

import csv
import io
import logging
import re
from datetime import datetime

import requests
from django.conf import settings
from requests import RequestException

from .models import JobAdvert, job_fingerprint

logger = logging.getLogger(__name__)

REQUIRED_COLS = {"title", "description"}


def _skill_extractor():
    from analysis.spacyskillextraction import SpacySkillExtractor

    return SpacySkillExtractor()


class AdzunaAPIError(Exception):
    def __init__(self, message, status_code=None, retryable=True, limit_reached=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.limit_reached = limit_reached


def import_from_csv(file_bytes: bytes) -> dict:
    """
    Import jobs from CSV bytes. Handles BOM, various encodings.
    Returns {"saved": N, "skipped": M, "errors": [...]}
    """
    # Try UTF-8 with BOM first, then latin-1 as fallback
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return {"saved": 0, "skipped": 0, "errors": ["Could not decode file. Save as UTF-8 CSV."]}

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        return {"saved": 0, "skipped": 0, "errors": ["CSV appears empty or has no header row."]}

    # Normalize header names (strip whitespace, lowercase)
    fieldnames_clean = [f.strip().lower() for f in reader.fieldnames]
    missing = REQUIRED_COLS - set(fieldnames_clean)
    if missing:
        return {
            "saved": 0, "skipped": 0,
            "errors": [f"Missing required columns: {', '.join(missing)}. Found: {', '.join(fieldnames_clean)}"]
        }

    saved, skipped, errors = 0, 0, []
    batch = []
    extractor = _skill_extractor()

    for i, raw_row in enumerate(reader, start=2):
        # Re-key with cleaned names
        row = {k.strip().lower(): v.strip() for k, v in raw_row.items() if k}

        title = row.get("title", "")
        description = row.get("description", "")

        if not title or not description:
            skipped += 1
            continue

        try:
            fingerprint = job_fingerprint(
                title,
                row.get("company", ""),
                row.get("location", ""),
                description,
                row.get("recruiter", ""),
                row.get("job_ref", "") or row.get("job_reference", ""),
            )
            if JobAdvert.objects.filter(fingerprint=fingerprint).exists():
                skipped += 1
                continue
            sections = extract_advert_sections(description)
            metadata = extract_advert_metadata(description)
            analysis_text = "\n\n".join(part for part in [
                title,
                row.get("company", ""),
                metadata.get("recruiter") or row.get("recruiter", ""),
                row.get("category", ""),
                row.get("summary", "") or sections["summary"],
                row.get("position_info", "") or sections["position_info"],
                description,
            ] if part)
            skill_entities = extractor.extract_entities(analysis_text, document_id=f"job-fingerprint-{fingerprint}")
            batch.append(JobAdvert(
                title=title[:255],
                company=row.get("company", "")[:255],
                recruiter=(row.get("recruiter", "") or metadata.get("recruiter") or row.get("company", ""))[:255],
                job_reference=(row.get("job_ref", "") or row.get("job_reference", "") or metadata.get("job_reference", ""))[:255],
                location=(row.get("location", "") or metadata.get("location", ""))[:255],
                category=row.get("category", "")[:255],
                contract_type=row.get("contract_type", "")[:80],
                contract_time=row.get("contract_time", "")[:80],
                summary=row.get("summary", "") or sections["summary"],
                position_info=row.get("position_info", "") or sections["position_info"],
                raw_description=description,
                description=description,
                url=row.get("url", "")[:500],
                source="csv",
                fingerprint=fingerprint,
                salary_min=_to_int(row.get("salary_min")),
                salary_max=_to_int(row.get("salary_max")),
                skills_extracted=sorted({entity["skill"] for entity in skill_entities}),
                skill_entities=skill_entities,
                date_posted=parse_advert_date(row.get("date_posted") or metadata.get("date_posted")),
            ))
            saved += 1
        except Exception as e:
            errors.append(f"Row {i}: {e}")

        # Bulk insert every 500 rows to avoid memory issues
        if len(batch) >= 500:
            JobAdvert.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []

    if batch:
        JobAdvert.objects.bulk_create(batch, ignore_conflicts=True)

    return {"saved": saved, "skipped": skipped, "errors": errors}

def _adzuna_credentials():
    app_id = settings.ADZUNA_APP_ID
    app_key = settings.ADZUNA_APP_KEY
    country = getattr(settings, "ADZUNA_COUNTRY", "za")

    if not app_id or not app_key:
        raise ValueError("Missing ADZUNA credentials")
    return app_id, app_key, country


def fetch_adzuna_page(keyword: str, location: str = "south africa", page: int = 1, per_page: int = 50, progress_callback=None) -> dict:
    app_id, app_key, country = _adzuna_credentials()
    per_page = max(1, min(50, int(per_page)))
    page = max(1, int(page))
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

    try:
        resp = requests.get(url, params={
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": per_page,
            "what": keyword,
            "where": location,
            "content-type": "application/json",
        }, headers={
            "Accept": "application/json",
        }, timeout=20)
    except RequestException as exc:
        raise AdzunaAPIError(f"Adzuna network error: {exc}", retryable=True) from exc

    if resp.status_code != 200:
        raise _adzuna_error_from_response(resp)

    data = resp.json()
    results = data.get("results", [])
    saved = 0
    duplicates = 0
    extractor = _skill_extractor()

    if progress_callback:
        progress_callback({
            "page": page,
            "saved": saved,
            "duplicates": duplicates,
            "seen": len(results),
            "processed": 0,
            "db_total": JobAdvert.objects.count(),
        })

    for index, item in enumerate(results, start=1):
        ext_id = str(item.get("id", ""))
        title = item.get("title", "")[:255]
        company = item.get("company", {}).get("display_name", "")[:255]
        recruiter = _value(item, "recruiter", "display_name") or company
        job_reference = str(item.get("job_ref") or item.get("job_reference") or item.get("reference") or "")[:255]
        location_name = item.get("location", {}).get("display_name", "")[:255]
        description = item.get("description", "")
        sections = extract_advert_sections(description)
        metadata = extract_advert_metadata(description)
        recruiter = (recruiter or metadata.get("recruiter") or company)[:255]
        job_reference = (job_reference or metadata.get("job_reference", ""))[:255]
        location_name = (location_name or metadata.get("location", ""))[:255]
        category = _value(item, "category", "label") or _value(item, "category", "tag") or ""
        fingerprint = job_fingerprint(title, company, location_name, description, recruiter, job_reference)
        analysis_text = "\n\n".join(part for part in [
            title,
            company,
            recruiter,
            category,
            sections["summary"],
            sections["position_info"],
            description,
        ] if part)
        skill_entities = extractor.extract_entities(analysis_text, document_id=f"job-fingerprint-{fingerprint}")

        if (ext_id and JobAdvert.objects.filter(source="adzuna", external_id=ext_id).exists()) or JobAdvert.objects.filter(fingerprint=fingerprint).exists():
            duplicates += 1
            if progress_callback:
                progress_callback({
                    "page": page,
                    "saved": saved,
                    "duplicates": duplicates,
                    "seen": len(results),
                    "processed": index,
                    "db_total": JobAdvert.objects.count(),
                })
            continue

        JobAdvert.objects.create(
            title=title,
            company=company,
            recruiter=recruiter[:255],
            job_reference=job_reference,
            location=location_name,
            category=category[:255],
            contract_type=str(item.get("contract_type") or "")[:80],
            contract_time=str(item.get("contract_time") or "")[:80],
            latitude=_to_float(item.get("latitude")),
            longitude=_to_float(item.get("longitude")),
            summary=sections["summary"],
            position_info=sections["position_info"],
            raw_description=description,
            description=description,
            url=item.get("redirect_url", ""),
            source="adzuna",
            external_id=ext_id,
            fingerprint=fingerprint,
            salary_min=item.get("salary_min"),
            salary_max=item.get("salary_max"),
            skills_extracted=sorted({entity["skill"] for entity in skill_entities}),
            skill_entities=skill_entities,
            source_payload=item,
            date_posted=_parse_date(item.get("created")) or _parse_date(metadata.get("date_posted")),
        )
        saved += 1
        if progress_callback:
            progress_callback({
                "page": page,
                "saved": saved,
                "duplicates": duplicates,
                "seen": len(results),
                "processed": index,
                "db_total": JobAdvert.objects.count(),
            })

    return {
        "page": page,
        "saved": saved,
        "duplicates": duplicates,
        "seen": len(results),
        "total_count": int(data.get("count") or 0),
        "db_total": JobAdvert.objects.count(),
        "has_more": len(results) == per_page,
    }


def fetch_from_adzuna(keyword: str, location: str = "south africa", max_results: int = 800, progress_callback=None) -> int:
    saved = 0
    page = 1
    per_page = 50

    while True:
        result = fetch_adzuna_page(keyword, location, page=page, per_page=per_page, progress_callback=progress_callback)
        if not result["seen"]:
            break

        saved += result["saved"]
        if saved >= max_results:
            return saved
        if not result["has_more"]:
            break

        page += 1

    return saved


def _adzuna_error_from_response(resp):
    detail = _extract_error_detail(resp)
    status = resp.status_code
    if status == 429:
        return AdzunaAPIError(
            f"Adzuna API limit reached or rate limited (HTTP 429). {detail}",
            status_code=status,
            retryable=True,
            limit_reached=True,
        )
    if status in (401, 403):
        return AdzunaAPIError(
            f"Adzuna authentication error (HTTP {status}). Check ADZUNA_APP_ID and ADZUNA_APP_KEY. {detail}",
            status_code=status,
            retryable=False,
        )
    if 400 <= status < 500:
        return AdzunaAPIError(
            f"Adzuna request error (HTTP {status}). {detail}",
            status_code=status,
            retryable=False,
        )
    return AdzunaAPIError(
        f"Adzuna server error (HTTP {status}). {detail}",
        status_code=status,
        retryable=True,
    )


def _extract_error_detail(resp):
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:300]
    for key in ("error", "message", "display_name", "description"):
        if data.get(key):
            return str(data[key])[:300]
    return str(data)[:300]


SECTION_LABELS = {
    "summary": ["summary", "job summary"],
    "position_info": ["position info", "position information", "key responsibilities", "qualifications", "preferred knowledge and experience"],
}

METADATA_LABELS = {
    "recruiter": ["recruiter", "agency", "consultant"],
    "job_reference": ["job ref", "job reference", "reference", "ref"],
    "date_posted": ["date posted", "posted", "posted date"],
    "location": ["location"],
}


def extract_advert_sections(text):
    sections = {"summary": "", "position_info": ""}
    if not text:
        return sections

    current = None
    buffers = {"summary": [], "position_info": []}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        label = _section_for_line(line)
        if label:
            current = label
            remainder = re.sub(r"^[A-Za-z /&+-]+:\s*", "", line).strip()
            if remainder and remainder.lower() != line.lower():
                buffers[current].append(remainder)
            continue
        if current:
            buffers[current].append(line)

    sections["summary"] = "\n".join(buffers["summary"]).strip()
    sections["position_info"] = "\n".join(buffers["position_info"]).strip()
    return sections


def extract_advert_metadata(text):
    metadata = {}
    lines = [line.strip() for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        if not line:
            continue
        clean = re.sub(r"[^a-z0-9 ]+", " ", line.lower()).strip()
        clean = re.sub(r"\s+", " ", clean)
        for field, labels in METADATA_LABELS.items():
            matching_label = next((label for label in labels if clean == label or clean.startswith(label + " ")), None)
            if not matching_label:
                continue
            value = _inline_label_value(line)
            if not value:
                value = _next_non_empty_line(lines, index + 1)
            if value:
                metadata[field] = value[:255]
    return metadata


def _section_for_line(line):
    clean = re.sub(r"[^a-z0-9 ]+", " ", line.lower()).strip()
    clean = re.sub(r"\s+", " ", clean)
    for section, labels in SECTION_LABELS.items():
        if any(clean == label or clean.startswith(label + " ") for label in labels):
            return section
    return None


def _inline_label_value(line):
    if ":" not in line:
        return ""
    return line.split(":", 1)[1].strip()


def _next_non_empty_line(lines, start):
    for line in lines[start:]:
        if line.strip():
            return line.strip()
    return ""


def _value(item, key, child):
    value = item.get(key)
    if isinstance(value, dict):
        return str(value.get(child) or "")
    return ""


def _to_int(val):
    try:
        return int(float(str(val).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_date(s):
    return parse_advert_date(s)


def parse_advert_date(s):
    if not s:
        return None
    for fmt in ("%A, %B %d, %Y", "%B %d, %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip()[:40], fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None
