# CurriculumMatch

Compare what your courses teach against what the job market demands, using Word2Vec skill matching.

**No Celery. No Redis. No WSL password needed.**
Background tasks run as Python threads inside Django.

---

## Before You Start

Use Python 3.12.9 for this project.

- Local development uses SQLite by default.
- Docker uses PostgreSQL.
- Do not use Python 3.14 for this project.

If you are just trying to run the app on your machine, use the local setup first.

## Quick Start (Docker)

Use Docker when you want the app and PostgreSQL to run together in containers.

Make sure Docker Desktop is running before you start.

Create a `.env` file in the project root before starting Docker.

```bash
docker compose up --build
# In a second terminal:
docker compose exec web python manage.py createsuperuser
```

Visit http://localhost:8000

---

## Local Setup (Windows / WSL / Linux)

This is the easiest way to run the project.

By default, local development uses the SQLite file already configured in the project:

```text
db.sqlite3
```

You do not need PostgreSQL for the default local setup.

### Prerequisites
- Python 3.12.9

### Install

Create the virtual environment with Python 3.12.9 explicitly:

Windows PowerShell:

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS / Linux / WSL:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If `py -3.12` or `python3.12` is not available, install Python 3.12.9 first and confirm it with:

```bash
py -3.12 --version
python3.12 --version
```

### Optional: create `.env`

You only need `.env` if you want to override defaults or use API-backed features.

```
SECRET_KEY=any-long-random-string
DEBUG=True
DB_NAME=curriculum_matcher
DB_USER=postgres
DB_PASSWORD=yourpassword
DB_HOST=localhost
DB_PORT=5432
```

### Migrate and run

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://localhost:8000

That is enough for normal local development.

---

## Database Modes

The project now supports two database modes:

### Local mode

- Default mode
- Uses SQLite
- No PostgreSQL required
- Good for development on one machine

### Docker mode

- Used by `docker compose up --build`
- Uses PostgreSQL in the `db` container
- Good when you want the app environment to match container deployment more closely
- Automatically runs `python manage.py seed_docker_postgres` on startup
- The seed step does not rely on an empty database; it skips when the fixture signature is already present

---

## Move SQLite Data Into Docker PostgreSQL

Use this when your local SQLite database has the data you want and your Docker app is already running against PostgreSQL.

If `sqlite_to_postgres.json` is committed to the repo, Docker startup can import it automatically on the first run. Later restarts skip the import when the seed records are already present, so normal job ingestion does not trigger duplicate key errors.

### 1. Back up the current Docker PostgreSQL database

```powershell
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | Out-File -Encoding utf8 postgres_before_sqlite_import.sql
```

### 2. Export the local SQLite data

Windows PowerShell:

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe manage.py dumpdata --exclude auth.permission --exclude contenttypes --indent 2 --output sqlite_to_postgres.json
```

This creates a Django fixture file from `db.sqlite3`.

### 3. Clear the Docker PostgreSQL-backed Django data

```powershell
docker compose exec web python manage.py flush --no-input
```

### 4. Load the SQLite export into Docker PostgreSQL

```powershell
docker compose exec web python manage.py loaddata sqlite_to_postgres.json
```

### 5. Verify the import

```powershell
docker compose exec web python manage.py shell -c "from django.apps import apps; models=[m for m in apps.get_models() if m._meta.app_label in {'courses','jobs','analysis','dashboard','course_scraper','methodology'}]; print({m.__name__: m.objects.count() for m in models})"
```

Notes:

- `flush` clears Django-managed data in the Docker database before the import. Back up first.
- The JSON fixture file is visible inside the container because Docker Compose mounts the project folder into `/app`.
- This moves database rows only. If you store uploaded files in `media`, back those up separately.

---

## Ollama

Ollama is optional and is used for local LLM-assisted features such as verification.

### When running Django locally

The app uses:

```text
http://127.0.0.1:11434
```

### When running Django in Docker

The app automatically switches to:

```text
http://host.docker.internal:11434
```

This lets the Docker container call Ollama running on your Windows host without changing code.

### What you need to do

- Install and run Ollama on your machine if you want verification or local LLM features.
- Pull the model you want to use, for example `ministral-3:3b`.
- If you do not need Ollama features, the rest of the app can still run.

---

## How background tasks work (no Celery)

```
User clicks "Queue Analysis" or uploads CSV
        ↓
View creates a TaskRecord (status=PENDING)
        ↓
threading.Thread(target=..., daemon=True).start()
        ↓
View returns immediately — browser gets the redirect
        ↓
Thread runs in background: imports CSV / trains Word2Vec / writes results
        ↓
TaskRecord updated → SUCCESS or FAILURE
        ↓
/tasks/ page auto-refreshes every 5s to show progress
```

Daemon threads are tied to the Django process lifetime — fine for a dev/internal server.
For production with gunicorn, this works the same way.

---

## Usage

| Step | URL | Action |
|------|-----|--------|
| 1 | `/courses/` | Add courses and modules with syllabus text |
| 2 | `/jobs/upload/` | Upload CSV or fetch from Adzuna |
| 3 | `/tasks/` | Watch background import progress |
| 4 | `/analysis/results/` | Click Queue Analysis |
| 5 | `/analysis/results/` | View match scores and skill gaps |

---

## LLM Skill Verification And Learning

Use Ollama verification when jobs or modules look under-extracted.

```bash
python manage.py verify_database --max-jobs 20 --max-modules 20 --model ministral-3:3b
```

To place Ollama suggestions in the human review queue:

```bash
python manage.py verify_database --max-jobs 20 --max-modules 20 --model ministral-3:3b --save-candidates
```

Then open `/data-export/?label_status=candidate`, inspect the evidence, correct the skill/type if needed, and save. Saved candidates become `reviewed` skills and are added to `skills_extracted`.

You can also use `/human-oversight/` as the review control room. It shows AI candidates, thin-extraction database checks, recent reviewed skills, and a form to queue verification from the browser. Verification runs in Background Tasks so the page does not wait for Ollama.

Approved rows are learned in two ways:

- The dynamic skill lexicon ignores candidates and can use reviewed skills in future extraction.
- BERT NER retraining can use reviewed job and course evidence:

```bash
python manage.py train_bert_skill_ner --reviewed-only --epochs 5
```

---

## CSV Format

| Column | Required |
|--------|----------|
| title | YES |
| description | YES |
| company | no |
| location | no |
| url | no |
| salary_min | no |
| salary_max | no |

---

## Dependencies

```
Django, psycopg2-binary, gensim, numpy, scikit-learn, requests, python-dotenv
```

No Celery. No Redis. No message broker.
