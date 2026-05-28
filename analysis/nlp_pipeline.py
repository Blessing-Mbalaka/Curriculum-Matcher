"""
NLP Pipeline: Word2Vec vectorisation + skill extraction + gap computation.
"""

import re
import logging
from collections import Counter
from typing import List, Tuple

import numpy as np
from gensim.models import Word2Vec
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# Extend this list with your institution's domain-specific terms
SKILL_KEYWORDS = [
    # Office tools
    "excel","word","powerpoint","outlook","microsoft office","google sheets","google docs",
    "tableau","power bi",
    # Data & programming
    "sql","mysql","postgresql","python","r","java","javascript","html","css",
    "django","flask","data analysis","data analytics","machine learning","artificial intelligence",
    # HR & business
    "human resources","hr","recruitment","payroll","performance management",
    "labour law","employment equity","organisational development","training and development",
    "onboarding","talent management","succession planning",
    # Finance
    "accounting","bookkeeping","financial reporting","budgeting","forecasting",
    "ifrs","gaap","tax","auditing","cost accounting","management accounting",
    # General professional
    "communication","project management","leadership","teamwork","problem solving",
    "critical thinking","time management","customer service","negotiation",
    "presentation","stakeholder management","change management",
    # Digital & marketing
    "digital marketing","seo","social media","content creation","google analytics",
    "email marketing","crm","salesforce",
    # Math & stats
    "mathematics","statistics","quantitative analysis","numeracy","financial modelling",
]

# Sort longest first so multi-word phrases match before single words
SKILL_KEYWORDS = sorted(set(SKILL_KEYWORDS), key=len, reverse=True)


def clean_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> List[str]:
    return clean_text(text).split()


def extract_skills(text: str) -> List[str]:
    cleaned = clean_text(text)
    found = []
    for skill in SKILL_KEYWORDS:
        if re.search(r"\b" + re.escape(skill) + r"\b", cleaned):
            found.append(skill)
    return sorted(found)


def train_word2vec(documents: List[str], vector_size: int = 100) -> Word2Vec:
    sentences = [tokenize(doc) for doc in documents if doc.strip()]
    if not sentences:
        raise ValueError("No text documents provided for Word2Vec training.")
    model = Word2Vec(
        sentences=sentences,
        vector_size=vector_size,
        window=5,
        min_count=1,
        workers=2,
        epochs=10,
        seed=42,
    )
    return model


def document_vector(model: Word2Vec, text: str) -> np.ndarray:
    tokens = tokenize(text)
    vecs = [model.wv[t] for t in tokens if t in model.wv]
    return np.mean(vecs, axis=0) if vecs else np.zeros(model.vector_size)


def compute_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if np.all(a == 0) or np.all(b == 0):
        return 0.0
    return float(cosine_similarity([a], [b])[0][0])


def compute_gap(course_skills: List[str], job_skills: List[str]) -> Tuple[list, list, list]:
    cs, js = set(course_skills), set(job_skills)
    return sorted(cs & js), sorted(js - cs), sorted(cs - js)


def build_skill_matrix(texts: List[str]) -> List[Tuple[str, int]]:
    all_skills = []
    for text in texts:
        all_skills.extend(extract_skills(text))
    return Counter(all_skills).most_common()
