import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-in-production")
DEBUG = os.environ.get("DEBUG", "True") == "True"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost 127.0.0.1").split()

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Project apps
    "courses",
    "jobs",
    "analysis",
    "dashboard",
    "course_scraper",
    "methodology",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": 30,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Johannesburg"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Adzuna API (optional)
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
ADZUNA_COUNTRY = os.environ.get("ADZUNA_COUNTRY", "za")

# Gemini API (optional)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_CLEANING_ENABLED = os.environ.get("GEMINI_CLEANING_ENABLED", "False") == "True"
GEMINI_PROMPT_MAX_CHARS = int(os.environ.get("GEMINI_PROMPT_MAX_CHARS", "24000"))

SPACY_MODEL_NAME = os.environ.get("SPACY_MODEL_NAME", "en_core_web_sm")
COURSE_SKILL_NER_MODEL_PATH = os.environ.get(
    "COURSE_SKILL_NER_MODEL_PATH",
    str(BASE_DIR / "models" / "course_skill_ner"),
)
BERT_SKILL_NER_MODEL_PATH = os.environ.get(
    "BERT_SKILL_NER_MODEL_PATH",
    str(BASE_DIR / "models" / "bert_skill_ner"),
)
BERT_SKILL_NER_ENABLED = os.environ.get("BERT_SKILL_NER_ENABLED", "True") == "True"
BERT_SKILL_NER_MIN_CONFIDENCE = float(os.environ.get("BERT_SKILL_NER_MIN_CONFIDENCE", "0.65"))
SKILL_REGEX_FALLBACK_ENABLED = os.environ.get("SKILL_REGEX_FALLBACK_ENABLED", "False") == "True"
SKILL_NOUN_CHUNK_MINING_ENABLED = os.environ.get("SKILL_NOUN_CHUNK_MINING_ENABLED", "False") == "True"
AUTO_TRAIN_COURSE_SKILL_NER = os.environ.get("AUTO_TRAIN_COURSE_SKILL_NER", "True") == "True"
COURSE_SKILL_NER_AUTO_EPOCHS = int(os.environ.get("COURSE_SKILL_NER_AUTO_EPOCHS", "8"))
COURSE_SKILL_NER_MIN_EXAMPLES = int(os.environ.get("COURSE_SKILL_NER_MIN_EXAMPLES", "5"))
SEMANTIC_SCORE_WEIGHT = float(os.environ.get("SEMANTIC_SCORE_WEIGHT", "0.55"))
SKILL_SCORE_WEIGHT = float(os.environ.get("SKILL_SCORE_WEIGHT", "0.20"))
CONFIDENCE_SCORE_WEIGHT = float(os.environ.get("CONFIDENCE_SCORE_WEIGHT", "0.15"))
DECISION_TREE_SCORE_WEIGHT = float(os.environ.get("DECISION_TREE_SCORE_WEIGHT", "0.10"))
TOP_MODULE_MATCH_COUNT = int(os.environ.get("TOP_MODULE_MATCH_COUNT", "3"))
SEMANTIC_EMBED_CHUNK_CHARS = int(os.environ.get("SEMANTIC_EMBED_CHUNK_CHARS", "3500"))
SEMANTIC_EMBED_MAX_CHUNKS = int(os.environ.get("SEMANTIC_EMBED_MAX_CHUNKS", "12"))

TINYLLAMA_MODEL = os.environ.get("TINYLLAMA_MODEL", "tinyllama")
TINYLLAMA_ENDPOINT = os.environ.get("TINYLLAMA_ENDPOINT", "http://127.0.0.1:11434/api/generate")
TINYLLAMA_TIMEOUT_SECONDS = int(os.environ.get("TINYLLAMA_TIMEOUT_SECONDS", "45"))
