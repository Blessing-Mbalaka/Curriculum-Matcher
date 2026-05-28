"""
Background task runner using Python threads — no Celery, no Redis needed.

Each function runs in a daemon thread so Django doesn't block waiting for it.
TaskRecord tracks progress exactly as before — the UI polls /api/task/<pk>/
and auto-refreshes until status is SUCCESS or FAILURE.
"""

import logging
import threading

from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _mark(record_id, status, notes="", progress=None):
    """Update a TaskRecord status. Safe to call from any thread."""
    from analysis.models import TaskRecord
    updates = {
        "status": status,
        "notes": notes,
        "finished_at": timezone.now() if status in ("SUCCESS", "FAILURE") else None,
    }
    if progress is not None:
        updates["progress"] = max(0, min(100, int(progress)))
    TaskRecord.objects.filter(id=record_id).update(**updates)


def _run_in_thread(fn, *args, **kwargs):
    """Spawn fn(*args, **kwargs) as a daemon thread and return immediately."""
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Task functions (called in threads)
# ---------------------------------------------------------------------------

def _do_gap_analysis(run_name, record_id):
    from analysis.services import run_gap_analysis
    _mark(record_id, "STARTED", "Preparing analysis...", 1)
    try:
        def report(percent, notes):
            _mark(record_id, "STARTED", notes, percent)

        run = run_gap_analysis(run_name=run_name, progress_callback=report)
        _mark(record_id, "SUCCESS", f"Done. AnalysisRun ID: {run.id}", 100)
    except Exception as exc:
        logger.error(f"Gap analysis failed: {exc}", exc_info=True)
        _mark(record_id, "FAILURE", str(exc))


def _do_csv_import(csv_bytes, record_id):
    from jobs.ingestion import import_from_csv
    _mark(record_id, "STARTED", "Importing CSV...", 10)
    try:
        result = import_from_csv(csv_bytes)
        notes = f"Saved: {result['saved']}, Skipped: {result['skipped']}"
        if result["errors"]:
            notes += f" | Errors: {'; '.join(str(e) for e in result['errors'][:3])}"
        _mark(record_id, "SUCCESS", notes, 100)
    except Exception as exc:
        logger.error(f"CSV import failed: {exc}", exc_info=True)
        _mark(record_id, "FAILURE", str(exc))


def _do_adzuna_fetch(keyword, location, max_results, record_id):
    from jobs.ingestion import fetch_from_adzuna
    _mark(record_id, "STARTED", "Fetching jobs from Adzuna...", 10)
    try:
        count = fetch_from_adzuna(keyword, location, max_results)
        _mark(record_id, "SUCCESS", f"Fetched {count} new jobs for '{keyword}'", 100)
    except Exception as exc:
        logger.error(f"Adzuna fetch failed: {exc}", exc_info=True)
        _mark(record_id, "FAILURE", str(exc))


# ---------------------------------------------------------------------------
# Public API — drop-in replacements for the old Celery .delay() calls
# ---------------------------------------------------------------------------

def run_gap_analysis_task(run_name="Analysis Run", record_id=None):
    """Start gap analysis in a background thread."""
    _run_in_thread(_do_gap_analysis, run_name, record_id)


def import_csv_task(csv_bytes, record_id=None):
    """Import CSV in a background thread. csv_bytes must be bytes."""
    _run_in_thread(_do_csv_import, csv_bytes, record_id)


def fetch_adzuna_task(keyword, location="south africa", max_results=50, record_id=None):
    """Fetch Adzuna jobs in a background thread."""
    _run_in_thread(_do_adzuna_fetch, keyword, location, max_results, record_id)
