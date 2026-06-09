"""
Synthetic NER training data generator for SKILL entity recognition.

Uses Gemini to produce diverse sentences that naturally contain a named skill,
then resolves the character offsets so the output is ready for token-classification
fine-tuning (HuggingFace) or spaCy training.

Output format (same as collect_training_examples):
    List of (text, {"entities": [(start, end, "SKILL"), ...]})

Typical usage
-------------
From a script or Django shell:

    from analysis.synthetic_data_gen import generate_synthetic_examples
    examples = generate_synthetic_examples(skills=["python", "sql"], per_skill=8)

Or via train_bert_ner.py --synthetic flag.
"""

import json
import logging
import re
import time
from typing import List, Optional, Tuple

import requests
from django.conf import settings

from .nlp_pipeline import SKILL_KEYWORDS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skill seed list
# ---------------------------------------------------------------------------

SYNTHETIC_SKILL_SEEDS = sorted(set([
    # Data analyst core
    "python", "sql", "r", "excel", "tableau", "power bi", "pandas", "numpy",
    "matplotlib", "seaborn", "data visualisation", "data wrangling", "data cleaning",
    "exploratory data analysis", "statistical analysis", "hypothesis testing",
    "regression analysis", "machine learning", "data modelling", "etl",
    "business intelligence", "looker", "dbt", "google analytics", "google sheets",
    "jupyter", "pivot tables", "power query", "data storytelling",
    # Engineering & development
    "javascript", "typescript", "java", "c#", "go", "rust", "kubernetes",
    "docker", "aws", "azure", "gcp", "ci/cd", "devops", "software engineering",
    "api design", "microservices", "cloud architecture", "system design",
    # General professional
    "project management", "stakeholder management", "communication",
    "critical thinking", "problem solving", "agile", "leadership",
    "change management", "presentation", "teamwork",
    # Finance & HR
    "financial modelling", "budgeting", "forecasting", "accounting",
    "ifrs", "recruitment", "performance management", "payroll",
] + SKILL_KEYWORDS), key=len, reverse=True)


# ---------------------------------------------------------------------------
# Prompt templates — varied contexts so the model sees diverse phrasing
# ---------------------------------------------------------------------------

PROMPT_TEMPLATES = [
    "academic course description",
    "job advert requirement",
    "graduate CV bullet point",
    "module learning outcome",
    "professional development course blurb",
]

_SYSTEM_CONTEXT = (
    "You generate realistic, diverse training sentences for a Named Entity "
    "Recognition system that labels professional skill mentions.\n"
    "Rules:\n"
    "- Each sentence must contain exactly the skill phrase as written.\n"
    "- Vary sentence length, register, and framing across the batch.\n"
    "- Do NOT alter the spelling or capitalisation of the skill phrase.\n"
    "- Return ONLY a JSON array of strings — no keys, no explanation."
)


def _build_prompt(skill: str, n: int, context_hint: str) -> str:
    return (
        f"{_SYSTEM_CONTEXT}\n\n"
        f"Context style: {context_hint}\n"
        f"Skill to embed: \"{skill}\"\n"
        f"Generate {n} sentences, each naturally containing \"{skill}\".\n"
        "Return a JSON array, e.g. [\"sentence one.\", \"sentence two.\"]"
    )


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def _gemini_generate(prompt: str, api_key: str, model: str, retries: int = 2) -> Optional[List[str]]:
    url = _GEMINI_ENDPOINT.format(model=model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.85,
            "maxOutputTokens": 1024,
        },
    }
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=45,
            )
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                logger.warning("Gemini rate limit hit; waiting %ss", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            text = (
                resp.json()
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "[]")
            )
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(s) for s in parsed if isinstance(s, str) and s.strip()]
        except Exception as exc:
            logger.warning("Gemini call failed (attempt %s): %s", attempt + 1, exc)
            time.sleep(2)
    return None


# ---------------------------------------------------------------------------
# Offset resolver
# ---------------------------------------------------------------------------

def _find_offsets(text: str, skill: str) -> Optional[Tuple[int, int]]:
    """Return the first (start, end) character offset of *skill* in *text*."""
    clean = " ".join(skill.strip().split())
    if not clean or not text:
        return None
    pattern = r"(?<!\w)" + re.escape(clean).replace(r"\ ", r"\s+") + r"(?!\w)"
    m = re.search(pattern, text, flags=re.IGNORECASE)
    return (m.start(), m.end()) if m else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_synthetic_examples(
    skills: Optional[List[str]] = None,
    per_skill: int = 6,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    progress_callback=None,
) -> List[Tuple[str, dict]]:
    """
    Generate synthetic NER training examples for each skill.

    Parameters
    ----------
    skills:
        List of skill strings to generate sentences for.
        Defaults to SYNTHETIC_SKILL_SEEDS.
    per_skill:
        Number of sentences to request per skill per context style.
        Total per skill = per_skill (spread across context templates).
    api_key:
        Gemini API key. Falls back to settings.GEMINI_API_KEY.
    model:
        Gemini model name. Falls back to settings.GEMINI_MODEL or gemini-2.5-flash.
    progress_callback:
        Optional callable(message: str) for progress reporting.

    Returns
    -------
    List of (text, {"entities": [(start, end, "SKILL")]}) tuples.
    """
    resolved_key = api_key or getattr(settings, "GEMINI_API_KEY", "")
    if not resolved_key:
        raise ValueError(
            "Gemini API key is required. Set GEMINI_API_KEY in settings or pass api_key=."
        )
    resolved_model = model or getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    skill_list = skills or SYNTHETIC_SKILL_SEEDS

    def report(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        logger.info(msg)

    examples: List[Tuple[str, dict]] = []
    total = len(skill_list)

    for idx, skill in enumerate(skill_list, start=1):
        report(f"[{idx}/{total}] Generating synthetic sentences for: {skill}")

        # Rotate context templates so we get variety
        context = PROMPT_TEMPLATES[idx % len(PROMPT_TEMPLATES)]
        n_per_call = max(2, per_skill)

        sentences = _gemini_generate(
            _build_prompt(skill, n_per_call, context),
            resolved_key,
            resolved_model,
        )
        if not sentences:
            report(f"  Skipped — no sentences returned for: {skill}")
            continue

        added = 0
        for sentence in sentences:
            offsets = _find_offsets(sentence, skill)
            if offsets is None:
                # Skill phrase wasn't preserved verbatim; skip
                continue
            start, end = offsets
            examples.append((sentence, {"entities": [(start, end, "SKILL")]}))
            added += 1

        report(f"  Added {added} examples (from {len(sentences)} sentences)")

    report(f"Synthetic generation complete: {len(examples)} examples across {total} skills.")
    return examples


def save_examples(examples: List[Tuple[str, dict]], path: str) -> None:
    """Serialise examples to JSON for reuse between training runs."""
    import json
    from pathlib import Path

    out = [{"text": text, "entities": ann["entities"]} for text, ann in examples]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved %s examples to %s", len(examples), path)


def load_examples(path: str) -> List[Tuple[str, dict]]:
    """Load examples previously saved by save_examples."""
    import json
    from pathlib import Path

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(item["text"], {"entities": [tuple(e) for e in item["entities"]]}) for item in raw]
