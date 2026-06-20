"""
Background task runner using Python threads — no Celery, no Redis needed.

Each function runs in a daemon thread so Django doesn't block waiting for it.
TaskRecord tracks progress exactly as before — the UI polls /api/task/<pk>/
and auto-refreshes until status is SUCCESS or FAILURE.
"""

import logging
import threading
import time

from django.utils import timezone

logger = logging.getLogger(__name__)
STOP_EVENTS = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _mark(record_id, status, notes="", progress=None):
    """Update a TaskRecord status. Safe to call from any thread."""
    from analysis.models import TaskRecord
    updates = {
        "status": status,
        "notes": notes,
        "updated_at": timezone.now(),
        "finished_at": timezone.now() if status in ("SUCCESS", "FAILURE", "STOPPED") else None,
    }
    if progress is not None:
        updates["progress"] = max(0, min(100, int(progress)))
    TaskRecord.objects.filter(id=record_id).update(**updates)


def _stopped(record_id):
    event = STOP_EVENTS.get(record_id)
    return bool(event and event.is_set())


def _run_in_thread(fn, *args, **kwargs):
    """Spawn fn(*args, **kwargs) as a daemon thread and return immediately."""
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Task functions (called in threads)
# ---------------------------------------------------------------------------

def _do_gap_analysis(run_name, record_id, max_jobs=None):
    from analysis.services import run_gap_analysis
    _mark(record_id, "STARTED", "Preparing analysis...", 1)
    try:
        def report(percent, notes):
            _mark(record_id, "STARTED", notes, percent)

        run = run_gap_analysis(run_name=run_name, progress_callback=report, max_jobs=max_jobs)
        _mark(record_id, "SUCCESS", f"Done. AnalysisRun ID: {run.id}", 100)
    except Exception as exc:
        logger.error(f"Gap analysis failed: {exc}", exc_info=True)
        _mark(record_id, "FAILURE", str(exc))


def _do_skill_verification(record_id, max_jobs, max_modules, use_llm, save_candidates, model):
    from analysis.verification import verify_database

    _mark(record_id, "STARTED", "Preparing database skill verification...", 5)
    progress_steps = [12, 25, 45, 65, 82]
    progress_index = 0
    try:
        def report(message):
            nonlocal progress_index
            progress = progress_steps[min(progress_index, len(progress_steps) - 1)]
            progress_index += 1
            _mark(record_id, "STARTED", message, progress)

        result = verify_database(
            max_jobs=max_jobs,
            max_modules=max_modules,
            use_llm=use_llm,
            save_candidates=save_candidates,
            model=model,
            progress_callback=report,
        )
        saved = result["summary"].get("candidate_skills_saved", 0)
        notes = (
            f"Verification complete. Checked {result['summary']['suspicious_records']} suspicious records; "
            f"saved {saved} candidate skill(s)."
        )
        _mark(record_id, "SUCCESS", notes, 100)
    except Exception as exc:
        logger.error("Skill verification failed: %s", exc, exc_info=True)
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


def _do_continuous_adzuna_fetch(keyword, location, max_results, interval_seconds, record_id):
    from jobs.ingestion import AdzunaAPIError, fetch_adzuna_page

    cycle = 0
    page = 1
    session_saved = 0
    try:
        while not _stopped(record_id):
            cycle += 1
            _mark(record_id, "STARTED", f"Jobs cycle {cycle}: fetching Adzuna page {page} for '{keyword}'...", 10)
            try:
                def report_fetch_progress(progress):
                    processed = progress["processed"]
                    seen = progress["seen"]
                    saved = progress["saved"]
                    duplicates = progress["duplicates"]
                    db_total = progress.get("db_total", 0)
                    pct = 15 + int(60 * processed / max(1, seen or max_results))
                    _mark(
                        record_id,
                        "STARTED",
                        f"Jobs cycle {cycle}: live count page {progress['page']} - saved {saved} this page, {session_saved + saved} this session, DB total {db_total}. Processed {processed}/{seen}, skipped {duplicates}.",
                        pct,
                    )

                result = fetch_adzuna_page(keyword, location, page=page, per_page=max_results, progress_callback=report_fetch_progress)
            except ValueError:
                raise
            except AdzunaAPIError as exc:
                logger.warning("Adzuna API issue during jobs-only cycle: %s", exc, exc_info=True)
                prefix = "Adzuna API limit reached" if exc.limit_reached else "Adzuna API error"
                if not exc.retryable:
                    raise
                _mark(record_id, "STARTED", f"Jobs cycle {cycle}: {prefix}. {exc} Retrying in {interval_seconds} seconds...", 20)
                for remaining in range(max(1, int(interval_seconds)), 0, -1):
                    if _stopped(record_id):
                        break
                    if remaining == interval_seconds or remaining <= 5 or remaining % 10 == 0:
                        _mark(record_id, "STARTED", f"Jobs cycle {cycle}: {prefix}. Retry in {remaining} seconds. Next Adzuna page: {page}.", 20)
                    time.sleep(1)
                continue
            except Exception as exc:
                logger.warning("Adzuna jobs-only cycle failed; retrying: %s", exc, exc_info=True)
                _mark(record_id, "STARTED", f"Jobs cycle {cycle}: fetch error ({exc}). Retrying in {interval_seconds} seconds...", 20)
                for remaining in range(max(1, int(interval_seconds)), 0, -1):
                    if _stopped(record_id):
                        break
                    if remaining == interval_seconds or remaining <= 5 or remaining % 10 == 0:
                        _mark(record_id, "STARTED", f"Jobs cycle {cycle}: retry in {remaining} seconds. Next Adzuna page: {page}.", 20)
                    time.sleep(1)
                continue
            if _stopped(record_id):
                break

            saved = result["saved"]
            duplicates = result["duplicates"]
            seen = result["seen"]
            total_count = result.get("total_count", 0)
            db_total = result.get("db_total", 0)
            session_saved += saved
            if not seen and total_count == 0:
                page = 1
                _mark(record_id, "STARTED", f"Jobs cycle {cycle}: Adzuna returned 0 results for '{keyword}' in '{location}'. DB total remains {db_total}. Waiting {interval_seconds} seconds before retrying.", 85)
            elif not seen:
                page = 1
                _mark(record_id, "STARTED", f"Jobs cycle {cycle}: reached the end of Adzuna results ({total_count} available). DB total {db_total}. Restarting from page 1 after {interval_seconds} seconds.", 85)
            elif seen and saved == 0 and duplicates == seen:
                if result["has_more"]:
                    page += 1
                else:
                    page = 1
                _mark(record_id, "STARTED", f"Jobs cycle {cycle}: page {result['page']} had {duplicates} duplicate jobs and 0 new saves. DB total remains {db_total}; {session_saved} new jobs saved this session. Next page: {page}.", 85)
            elif seen and result["has_more"]:
                page += 1
            else:
                page = 1

            if seen and not (saved == 0 and duplicates == seen):
                _mark(record_id, "STARTED", f"Jobs cycle {cycle}: page {result['page']} added {saved} new jobs, skipped {duplicates} duplicates out of {seen}. DB total {db_total}; {session_saved} new this session. Total available: {total_count}. Waiting {interval_seconds} seconds before the next fetch...", 85)
            for remaining in range(max(1, int(interval_seconds)), 0, -1):
                if _stopped(record_id):
                    break
                if remaining == interval_seconds or remaining <= 5 or remaining % 10 == 0:
                    _mark(record_id, "STARTED", f"Jobs cycle {cycle}: waiting {remaining} seconds, still active. DB total {db_total}; {session_saved} new this session. Next Adzuna page: {page}.", 90)
                time.sleep(1)

        _mark(record_id, "STOPPED", "Jobs-only fetch loop paused by user.", 100)
    except Exception as exc:
        logger.error(f"Continuous Adzuna fetch failed: {exc}", exc_info=True)
        _mark(record_id, "FAILURE", str(exc))
    finally:
        STOP_EVENTS.pop(record_id, None)


def _do_continuous_job_cycle(keyword, location, max_results, interval_seconds, record_id):
    from analysis.services import run_gap_analysis
    from jobs.ingestion import AdzunaAPIError, fetch_from_adzuna

    cycle = 0
    try:
        while not _stopped(record_id):
            cycle += 1
            _mark(record_id, "STARTED", f"Cycle {cycle}: fetching jobs for '{keyword}'...", 5)
            try:
                def report_fetch_progress(progress):
                    processed = progress["processed"]
                    seen = progress["seen"]
                    saved_so_far = progress["saved"]
                    duplicates = progress["duplicates"]
                    pct = 5 + int(25 * processed / max(1, seen or max_results))
                    _mark(
                        record_id,
                        "STARTED",
                        f"Cycle {cycle}: fetching Adzuna page {progress['page']} - saved {saved_so_far}, processed {processed}/{seen}, skipped {duplicates}.",
                        pct,
                    )

                saved = fetch_from_adzuna(keyword, location, max_results, progress_callback=report_fetch_progress)
            except AdzunaAPIError as exc:
                logger.warning("Adzuna API issue during live pipeline cycle: %s", exc, exc_info=True)
                if not exc.retryable:
                    raise
                prefix = "Adzuna API limit reached" if exc.limit_reached else "Adzuna network/API error"
                _mark(record_id, "STARTED", f"Cycle {cycle}: {prefix}. {exc} Retrying in {interval_seconds} seconds...", 20)
                for remaining in range(max(1, int(interval_seconds)), 0, -1):
                    if _stopped(record_id):
                        break
                    if remaining == interval_seconds or remaining <= 5 or remaining % 10 == 0:
                        _mark(record_id, "STARTED", f"Cycle {cycle}: {prefix}. Retry in {remaining} seconds.", 20)
                    time.sleep(1)
                continue
            if _stopped(record_id):
                break

            _mark(record_id, "STARTED", f"Cycle {cycle}: fetched {saved} new jobs. Running analysis...", 35)

            def report(percent, notes):
                if _stopped(record_id):
                    raise InterruptedError("Live pipeline paused by user.")
                mapped = 35 + int(55 * max(0, min(100, percent)) / 100)
                _mark(record_id, "STARTED", f"Cycle {cycle}: {notes}", mapped)

            run = run_gap_analysis(run_name=f"Live Run {cycle}", progress_callback=report)
            _mark(record_id, "STARTED", f"Cycle {cycle}: analysis #{run.id} complete. Waiting for next fetch...", 95)

            for remaining in range(max(1, int(interval_seconds)), 0, -1):
                if _stopped(record_id):
                    break
                if remaining == interval_seconds or remaining <= 5 or remaining % 10 == 0:
                    _mark(record_id, "STARTED", f"Cycle {cycle}: waiting {remaining} seconds before the next fetch.", 95)
                time.sleep(1)

        _mark(record_id, "STOPPED", "Live pipeline paused by user.", 100)
    except InterruptedError:
        _mark(record_id, "STOPPED", "Live pipeline paused by user.", 100)
    except Exception as exc:
        logger.error(f"Continuous job cycle failed: {exc}", exc_info=True)
        _mark(record_id, "FAILURE", str(exc))
    finally:
        STOP_EVENTS.pop(record_id, None)


# ---------------------------------------------------------------------------
# Public API — drop-in replacements for the old Celery .delay() calls
# ---------------------------------------------------------------------------

def run_gap_analysis_task(run_name="Analysis Run", record_id=None, max_jobs=None):
    """Start gap analysis in a background thread."""
    _run_in_thread(_do_gap_analysis, run_name, record_id, max_jobs)


def start_skill_verification_task(record_id=None, max_jobs=10, max_modules=10, use_llm=True, save_candidates=True, model=None):
    """Verify skill extraction in a background thread."""
    _run_in_thread(_do_skill_verification, record_id, max_jobs, max_modules, use_llm, save_candidates, model)


def import_csv_task(csv_bytes, record_id=None):
    """Import CSV in a background thread. csv_bytes must be bytes."""
    _run_in_thread(_do_csv_import, csv_bytes, record_id)


def fetch_adzuna_task(keyword, location="south africa", max_results=50, record_id=None):
    """Fetch Adzuna jobs in a background thread."""
    _run_in_thread(_do_adzuna_fetch, keyword, location, max_results, record_id)


def start_continuous_job_task(keyword, location="south africa", max_results=50, interval_seconds=30, record_id=None):
    """Continuously fetch jobs and run gap analysis until paused."""
    STOP_EVENTS[record_id] = threading.Event()
    _run_in_thread(_do_continuous_job_cycle, keyword, location, max_results, interval_seconds, record_id)


def start_continuous_adzuna_task(keyword, location="south africa", max_results=50, interval_seconds=30, record_id=None):
    """Continuously fetch Adzuna jobs only until paused."""
    STOP_EVENTS[record_id] = threading.Event()
    _run_in_thread(_do_continuous_adzuna_fetch, keyword, location, max_results, interval_seconds, record_id)


def stop_task(record_id):
    event = STOP_EVENTS.get(record_id)
    if event:
        event.set()
        _mark(record_id, "STARTED", "Pause requested. Finishing the current step...", None)
        return True
    _mark(record_id, "STOPPED", "Pause requested.", 100)
    return False
