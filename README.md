# CurriculumMatch

Compare what your courses teach against what the job market demands, using Word2Vec skill matching.

**No Celery. No Redis. No WSL password needed.**
Background tasks run as Python threads inside Django — just PostgreSQL and the Django dev server.

---

## Quick Start (Docker)

```bash
cp .env.example .env
docker compose up --build
# In a second terminal:
docker compose exec web python manage.py createsuperuser
```

Visit http://localhost:8000

---

## Local Setup (Windows / WSL / Linux)

### Prerequisites
- Python 3.11+
- PostgreSQL running locally (or via Docker)

### Install
```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Configure `.env`
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

That's it — **no second terminal, no worker process, no Redis**.

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
