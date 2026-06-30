# Remote Server Configuration Over SSH

Use these steps on the Linux server to configure Django environment variables such as `ALLOWED_HOSTS`.

## Important: `.env` is not copied by git

This project ignores `.env`, so the server will not get that file from `git pull`.

You must create the `.env` file manually on the server if it does not already exist.

## 1. Connect to the server

From your local machine:

```bash
ssh root@162.35.167.180
```

If you use another user, replace `root` with that username.

## 2. Go to the project folder

```bash
cd ~/projects/Curriculum-Matcher
```

If your project is in a different location, change to that path instead.

## 3. Edit the `.env` file

Open the file with `nano`:

```bash
nano .env
```

If the file does not exist yet, `nano` will create it when you save.

Use a complete `.env` file, not just `ALLOWED_HOSTS`. These values below are copied from your current local `.env` plus code defaults for anything not explicitly set there:

```env
SECRET_KEY=change-me-to-a-long-random-string
DEBUG=True
ALLOWED_HOSTS=localhost 127.0.0.1 162.35.167.180

# Database
USE_SQLITE=True

# Runtime
RUNNING_IN_DOCKER=False

# Adzuna API (optional)
ADZUNA_APP_ID=869909f5
ADZUNA_APP_KEY= e72546bd77582b62a96a1a50f706a17d
ADZUNA_COUNTRY=za

# Gemini API (optional)
GEMINI_API_KEY=AIzaSyAyYfLV6qdG4I6PlE8ZdMJCFz8A6zftIcw
GEMINI_MODEL=gemini-3.5-flash
GEMINI_CLEANING_ENABLED=True
GEMINI_PROMPT_MAX_CHARS=24000

# spaCy / skill extraction
SPACY_MODEL_NAME=en_core_web_sm
COURSE_SKILL_NER_MODEL_PATH=/root/projects/Curriculum-Matcher/models/course_skill_ner
BERT_SKILL_NER_MODEL_PATH=/root/projects/Curriculum-Matcher/models/bert_skill_ner
BERT_SKILL_NER_ENABLED=True
BERT_SKILL_NER_MIN_CONFIDENCE=0.65
SKILL_REGEX_FALLBACK_ENABLED=False
SKILL_NOUN_CHUNK_MINING_ENABLED=False
DYNAMIC_SKILL_LEXICON_ENABLED=True
DYNAMIC_SKILL_LEXICON_REVIEWED_ONLY=False
DYNAMIC_SKILL_LEXICON_MIN_FREQUENCY=1
DYNAMIC_SKILL_LEXICON_MAX_TERMS=1500
DYNAMIC_SKILL_LEXICON_ALLOW_EXCLUDED=True
DYNAMIC_SKILL_LEXICON_CSV_PATH=
AUTO_TRAIN_COURSE_SKILL_NER=True
COURSE_SKILL_NER_AUTO_EPOCHS=8
COURSE_SKILL_NER_MIN_EXAMPLES=5

# Scoring
SEMANTIC_SCORE_WEIGHT=0.55
SKILL_SCORE_WEIGHT=0.20
CONFIDENCE_SCORE_WEIGHT=0.15
DECISION_TREE_SCORE_WEIGHT=0.10
TOP_MODULE_MATCH_COUNT=3
SEMANTIC_EMBED_CHUNK_CHARS=3500
SEMANTIC_EMBED_MAX_CHUNKS=12
SEMANTIC_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
OLLAMA_EMBED_MODEL=nomic-embed-text

# TinyLlama / Ollama
TINYLLAMA_MODEL=tinyllama
TINYLLAMA_ENDPOINT=http://127.0.0.1:11434/api/generate
TINYLLAMA_TIMEOUT_SECONDS=45

# Verification model / Ollama
OLLAMA_VERIFICATION_MODEL=ministral-3:3b
OLLAMA_VERIFICATION_ENDPOINT=http://127.0.0.1:11434/api/generate
OLLAMA_VERIFICATION_TIMEOUT_SECONDS=90
OLLAMA_VERIFICATION_PROMPT_MAX_CHARS=9000

# Optional native export toggle used by research paper generation
CURRICULUMMATCH_ENABLE_NATIVE_PLOTLY_EXPORT=false
```

If you want to use PostgreSQL on the server instead of SQLite, use this instead:

```env
USE_SQLITE=False
DB_NAME=Jobs
DB_USER=postgres
DB_PASSWORD=Revolution88@
DB_HOST=localhost
DB_PORT=5432
```

When `USE_SQLITE=True`, the `DB_*` values are ignored.

Minimum values for the SQLite setup:

```env
SECRET_KEY=change-me-to-a-long-random-string
DEBUG=True
ALLOWED_HOSTS=localhost 127.0.0.1 162.35.167.180
USE_SQLITE=True
```

Save and exit in `nano`:

```text
Ctrl+O, Enter, Ctrl+X
```

## 3a. Confirm the file exists

Run:

```bash
ls -la .env
cat .env
```

If `cat .env` shows the values you added, the server configuration file is in place.

## 3b. Generate a Django secret key if needed

If you decide to replace the current `SECRET_KEY`, run this on the server:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copy the output into the `SECRET_KEY=` line in `.env`.

## 4. Restart the application

Restart the service that runs Django so the new environment variable is loaded.

If you use `gunicorn` with `systemd`:

```bash
sudo systemctl restart gunicorn
```

If you use Docker Compose:

```bash
docker compose up -d
```

If you use a custom service:

```bash
sudo systemctl restart <your-service-name>
```

## 4a. Set up gunicorn so it runs without SSH

If you do not want to keep an SSH session open, create a `systemd` service for gunicorn.

### Install gunicorn into the project virtual environment

```bash
cd ~/projects/Curriculum-Matcher
source .venv/bin/activate
pip install gunicorn
```

If you reinstall project dependencies later, `gunicorn` is also listed in `requirements.txt`.

### Stop `runserver` before starting gunicorn

If `python manage.py runserver` is still running on port `8000`, stop it first with `Ctrl+C` in that SSH terminal.

### Create the service file

Run:

```bash
sudo nano /etc/systemd/system/gunicorn.service
```

Paste this:

```ini
[Unit]
Description=Gunicorn for Curriculum Matcher
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=/root/projects/Curriculum-Matcher
ExecStart=/root/projects/Curriculum-Matcher/.venv/bin/gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

This app already calls `load_dotenv()` in Django settings, so the service does not need a separate `EnvironmentFile=` entry as long as `.env` is in `/root/projects/Curriculum-Matcher`.

Save and exit:

```text
Ctrl+O, Enter, Ctrl+X
```

### Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable gunicorn
sudo systemctl start gunicorn
```

### Check that it is running

```bash
sudo systemctl status gunicorn --no-pager
```

If it fails, inspect logs with:

```bash
journalctl -u gunicorn -n 100 --no-pager
```

### After this is enabled

- The app starts automatically after a reboot.
- The app keeps running after you disconnect SSH.
- You no longer need `python manage.py runserver` for normal use.

## 4b. Install and run Ollama on the VPS

Ollama is optional. You only need it if you want local LLM-backed features such as verification, TinyLlama generation, or Ollama embeddings.

This project already points to Ollama on:

```text
http://127.0.0.1:11434
```

so if Ollama runs on the same VPS, no extra Django setting change is required.

### Install Ollama

Run:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Check that it installed:

```bash
ollama --version
```

### Start Ollama

On many Linux installs, Ollama provides a `systemd` service. Try:

```bash
systemctl enable ollama
systemctl start ollama
systemctl status ollama --no-pager
```

If that service does not exist, run it manually in a terminal:

```bash
ollama serve
```

If you run it manually, it will stop when that terminal stops. The `systemd` service is the better setup.

### Pull the models this project is likely to use

Verification model from current settings:

```bash
ollama pull ministral-3:3b
```

TinyLlama model from current settings:

```bash
ollama pull tinyllama
```

Embedding model used by semantic similarity when Ollama embeddings are available:

```bash
ollama pull nomic-embed-text
```

### Test Ollama locally on the VPS

Check the API is responding:

```bash
curl http://127.0.0.1:11434/api/tags
```

Quick generation test:

```bash
ollama run tinyllama "Say hello in one sentence."
```

### Verify Django can use it

Because your `.env` already points these endpoints to `127.0.0.1:11434`, the app should call the local Ollama server automatically once it is running.

To verify the embedding backend specifically, run:

```bash
cd ~/projects/Curriculum-Matcher
source .venv/bin/activate
python manage.py check_embedding_backend --require-embedding --skip-checks
```

Or use the repo helper script:

```bash
cd ~/projects/Curriculum-Matcher
bash scripts/ensure_embeddings_ubuntu.sh
```

If that command exits with a Word2Vec fallback warning, one of these is usually true:

- Ollama is not running on `127.0.0.1:11434`
- `nomic-embed-text` has not been pulled on the VPS
- the server cannot load `sentence-transformers/all-MiniLM-L6-v2`

You can test the verification flow with:

```bash
cd ~/projects/Curriculum-Matcher
source .venv/bin/activate
python manage.py verify_database --max-jobs 5 --max-modules 5 --model ministral-3:3b
```

### Important notes for a small VPS

- Ollama models consume RAM and disk space.
- `ministral-3:3b` is much heavier than `tinyllama`.
- CPU-only VPS installs work, but responses can be slow.
- If disk space is tight, pull only the models you actually use.

## 5. Verify the app is reachable

Open the site in a browser using:

```text
http://162.35.167.180/
```

If you still get a Django host error, confirm the server is loading the same `.env` file you edited.

## Optional: set the variable directly in a systemd service

If your app does not use a `.env` file, add the variable to the service definition:

```ini
Environment="ALLOWED_HOSTS=localhost 127.0.0.1 162.35.167.180"
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart <your-service-name>
```
