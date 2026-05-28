"""
Job ingestion: CSV upload and Adzuna API.
"""

import csv
import io
import logging
from datetime import datetime

import requests
from django.conf import settings

from .models import JobAdvert

logger = logging.getLogger(__name__)

REQUIRED_COLS = {"title", "description"}


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

    for i, raw_row in enumerate(reader, start=2):
        # Re-key with cleaned names
        row = {k.strip().lower(): v.strip() for k, v in raw_row.items() if k}

        title = row.get("title", "")
        description = row.get("description", "")

        if not title or not description:
            skipped += 1
            continue

        try:
            batch.append(JobAdvert(
                title=title[:255],
                company=row.get("company", "")[:255],
                location=row.get("location", "")[:255],
                description=description,
                url=row.get("url", "")[:500],
                source="csv",
                salary_min=_to_int(row.get("salary_min")),
                salary_max=_to_int(row.get("salary_max")),
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


# import requests
# from django.conf import settings

import requests
from django.conf import settings

def fetch_from_adzuna(keyword: str, location: str = "south africa", max_results: int = 800) -> int:
    app_id = settings.ADZUNA_APP_ID
    app_key = settings.ADZUNA_APP_KEY
    country = getattr(settings, "ADZUNA_COUNTRY", "za")

    if not app_id or not app_key:
        raise ValueError("Missing ADZUNA credentials")

    saved = 0
    page = 1
    per_page = 50

    while True:
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

        resp = requests.get(url, params={
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": per_page,
            "what": keyword,
            "where": location,
        }, timeout=20)

        if resp.status_code != 200:
            print("Adzuna error response:", resp.text)
            resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])

        if not results:
            break

        for item in results:
            ext_id = str(item.get("id", ""))

            if ext_id and JobAdvert.objects.filter(external_id=ext_id).exists():
                continue

            JobAdvert.objects.create(
                title=item.get("title", "")[:255],
                company=item.get("company", {}).get("display_name", "")[:255],
                location=item.get("location", {}).get("display_name", "")[:255],
                description=item.get("description", ""),
                url=item.get("redirect_url", ""),
                source="adzuna",
                external_id=ext_id,
                salary_min=item.get("salary_min"),
                salary_max=item.get("salary_max"),
                date_posted=_parse_date(item.get("created")),
            )

            saved += 1

            if saved >= max_results:
                return saved

        if len(results) < per_page:
            break

        page += 1

    return saved


def _to_int(val):
    try:
        return int(float(str(val).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None
