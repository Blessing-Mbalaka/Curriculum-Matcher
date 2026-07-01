"""
Google Colab / notebook helper.

Reads a CSV of jobs, generates:
  - extracted skills
  - skill_entities
  - sentence embeddings

Writes a JSONL file that Django can import with:
  python manage.py import_embedded_jobs imports/embedded_jobs/jobs_embedded.jsonl --run-analysis --max-jobs 300
"""

from pathlib import Path
import json
import re

import pandas as pd
from sentence_transformers import SentenceTransformer


INPUT_CSV = "/content/jobs.csv"
OUTPUT_JSONL = "/content/jobs_embedded.jsonl"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


DEFAULT_SKILL_LEXICON = [
    "accounting",
    "analytics",
    "auditing",
    "bookkeeping",
    "budgeting",
    "business analysis",
    "communication",
    "crm",
    "customer service",
    "data analysis",
    "digital marketing",
    "excel",
    "financial modelling",
    "financial reporting",
    "forecasting",
    "leadership",
    "machine learning",
    "management accounting",
    "microsoft office",
    "negotiation",
    "payroll",
    "power bi",
    "presentation",
    "problem solving",
    "project management",
    "python",
    "recruitment",
    "salesforce",
    "seo",
    "social media",
    "sql",
    "stakeholder management",
    "statistics",
    "strategy",
    "tableau",
    "tax",
]


def normalize_skill(value):
    return " ".join(str(value or "").lower().replace("-", " ").split())


def find_skills(text, skill_lexicon):
    normalized_text = " " + re.sub(r"[^a-z0-9+# ]+", " ", str(text or "").lower()) + " "
    found = []
    for skill in skill_lexicon:
        needle = f" {skill} "
        if needle in normalized_text:
            found.append(skill)
    return sorted(set(found))


def build_skill_entities(skills):
    entities = []
    for skill in skills:
        entities.append({
            "skill": skill,
            "id": f"skill-{skill.replace(' ', '-')}",
            "source": "google-colab",
            "confidence": 0.9,
            "label": "SKILL",
            "label_status": "reviewed",
        })
    return entities


def main():
    df = pd.read_csv(INPUT_CSV)
    required = {"title", "description"}
    missing = required - set(df.columns.str.lower())
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    normalized_columns = {column: column.strip().lower() for column in df.columns}
    df = df.rename(columns=normalized_columns)

    model = SentenceTransformer(MODEL_NAME)
    skill_lexicon = [normalize_skill(skill) for skill in DEFAULT_SKILL_LEXICON]

    output_path = Path(OUTPUT_JSONL)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as handle:
        for index, row in df.iterrows():
            title = str(row.get("title") or "").strip()
            description = str(row.get("description") or "").strip()
            if not title or not description:
                continue

            analysis_text = "\n\n".join(
                value for value in [
                    title,
                    str(row.get("company") or "").strip(),
                    str(row.get("category") or "").strip(),
                    str(row.get("summary") or "").strip(),
                    str(row.get("position_info") or "").strip(),
                    description,
                ] if value
            )

            skills = find_skills(analysis_text, skill_lexicon)
            vector = model.encode(analysis_text, normalize_embeddings=True).tolist()

            payload = {
                "external_id": str(row.get("external_id") or f"notebook-{index + 1:06d}"),
                "title": title,
                "company": str(row.get("company") or "").strip(),
                "recruiter": str(row.get("recruiter") or "").strip(),
                "job_reference": str(row.get("job_reference") or "").strip(),
                "location": str(row.get("location") or "").strip(),
                "category": str(row.get("category") or "").strip(),
                "contract_type": str(row.get("contract_type") or "").strip(),
                "contract_time": str(row.get("contract_time") or "").strip(),
                "summary": str(row.get("summary") or "").strip(),
                "position_info": str(row.get("position_info") or "").strip(),
                "description": description,
                "url": str(row.get("url") or "").strip(),
                "salary_min": row.get("salary_min"),
                "salary_max": row.get("salary_max"),
                "skills_extracted": skills,
                "skill_entities": build_skill_entities(skills),
                "vector": vector,
                "cleaned_payload": {
                    "required_skills": skills,
                    "tools": [skill for skill in skills if skill in {"excel", "power bi", "python", "salesforce", "sql", "tableau"}],
                    "soft_skills": [skill for skill in skills if skill in {"communication", "leadership", "negotiation", "presentation", "problem solving", "stakeholder management"}],
                    "sector": str(row.get("category") or "").strip(),
                    "clean_title": title,
                },
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
