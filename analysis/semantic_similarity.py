"""
Semantic similarity service for curriculum-to-job matching.

Backend priority:
  1. Ollama nomic-embed-text (local, high quality, no API key)
  2. Sentence-BERT (local, requires sentence-transformers package)
  3. Word2Vec (lightweight fallback, always available)
"""

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from django.conf import settings

from .nlp_pipeline import compute_similarity, document_vector, train_word2vec

logger = logging.getLogger(__name__)


@dataclass
class ScoreBreakdown:
    semantic_score: float
    skill_score: float
    confidence_score: float
    decision_tree_score: float
    final_score: float
    model: str

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "semantic_score": round(self.semantic_score, 6),
            "skill_score": round(self.skill_score, 6),
            "confidence_score": round(self.confidence_score, 6),
            "decision_tree_score": round(self.decision_tree_score, 6),
            "final_score": round(self.final_score, 6),
        }


class SemanticSimilarityService:
    def __init__(self, corpus: Iterable[str], progress_callback=None):
        self.model_name = getattr(settings, "SEMANTIC_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
        self.semantic_weight = float(getattr(settings, "SEMANTIC_SCORE_WEIGHT", 0.75))
        self.skill_weight = float(getattr(settings, "SKILL_SCORE_WEIGHT", 0.25))
        self.confidence_weight = float(getattr(settings, "CONFIDENCE_SCORE_WEIGHT", 0.15))
        self.decision_tree_weight = float(getattr(settings, "DECISION_TREE_SCORE_WEIGHT", 0.10))
        self.top_module_count = int(getattr(settings, "TOP_MODULE_MATCH_COUNT", 3))
        self.embed_chunk_chars = max(500, int(getattr(settings, "SEMANTIC_EMBED_CHUNK_CHARS", 3500)))
        self.embed_max_chunks = max(1, int(getattr(settings, "SEMANTIC_EMBED_MAX_CHUNKS", 12)))
        self.backend = "word2vec"
        self.model = None
        self.embedding_dim = None
        self.embedding_failures = 0
        self._progress_callback = progress_callback
        documents = [text for text in corpus if text and text.strip()]

        self._load_ollama()
        if self.model is None:
            self._load_sentence_transformer()
        if self.model is None:
            self._report("No embedding model available. Falling back to Word2Vec scoring.")
            self.model = train_word2vec(documents)

    def _report(self, message: str) -> None:
        if self._progress_callback:
            self._progress_callback(message)

    def _load_ollama(self) -> None:
        ollama_model = getattr(settings, "OLLAMA_EMBED_MODEL", "nomic-embed-text")
        try:
            import ollama as _ollama
        except ImportError:
            logger.info("ollama package not installed; skipping Ollama backend.")
            return

        try:
            self._report(f"Connecting to Ollama ({ollama_model})...")
            # Verify the model is available by embedding a short probe string
            probe = _ollama.embeddings(model=ollama_model, prompt="ping")
            self.embedding_dim = len(probe.get("embedding") or []) or None
            self.model = ollama_model
            self.backend = "ollama"
            self._ollama = _ollama
            self._report(f"Ollama backend ready ({ollama_model}).")
        except Exception as exc:
            logger.warning("Ollama not available: %s", exc)
            self.model = None

    def _load_sentence_transformer(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.info("sentence-transformers is not installed; using Word2Vec fallback.")
            return

        try:
            self._report(f"Loading semantic model {self.model_name}...")
            self.model = SentenceTransformer(self.model_name)
            self.embedding_dim = self.model.get_sentence_embedding_dimension()
            self.backend = "sentence-transformers"
        except Exception as exc:
            logger.warning("Could not load sentence transformer model: %s", exc, exc_info=True)
            self.model = None

    def vectorize(self, text: str) -> np.ndarray:
        try:
            chunks = self._embedding_chunks(text)
            if len(chunks) > 1 and self.backend in {"ollama", "sentence-transformers"}:
                vectors = [self._vectorize_chunk(chunk) for chunk in chunks]
                vec = np.mean(vectors, axis=0)
                norm = np.linalg.norm(vec)
                return vec / norm if norm > 0 else vec
            return self._vectorize_chunk(chunks[0] if chunks else "")
        except Exception as exc:
            if self.backend in {"ollama", "sentence-transformers"}:
                self.embedding_failures += 1
                message = (
                    "Semantic embedding failed for one document; "
                    "continuing analysis with a neutral semantic vector."
                )
                logger.warning("%s Backend error: %s", message, exc)
                self._report(message)
                return self._neutral_vector()
            raise

    def _vectorize_chunk(self, text: str) -> np.ndarray:
        if self.backend == "ollama":
            response = self._ollama.embeddings(model=self.model, prompt=text or "")
            vec = np.asarray(response["embedding"], dtype=float)
            self.embedding_dim = len(vec) or self.embedding_dim
            norm = np.linalg.norm(vec)
            return vec / norm if norm > 0 else vec
        if self.backend == "sentence-transformers":
            return np.asarray(self.model.encode(text or "", normalize_embeddings=True), dtype=float)
        return document_vector(self.model, text or "")

    def _neutral_vector(self) -> np.ndarray:
        dim = self.embedding_dim or 1
        return np.zeros(dim, dtype=float)

    def _embedding_chunks(self, text: str) -> list[str]:
        text = " ".join((text or "").split())
        if not text:
            return [""]
        chunk_chars = getattr(self, "embed_chunk_chars", 3500)
        max_chunks = getattr(self, "embed_max_chunks", 12)
        if len(text) <= chunk_chars:
            return [text]

        chunks = []
        remaining = text
        while remaining and len(chunks) < max_chunks:
            if len(remaining) <= chunk_chars:
                chunks.append(remaining)
                break
            split_at = remaining.rfind(" ", 0, chunk_chars)
            if split_at < int(chunk_chars * 0.65):
                split_at = chunk_chars
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        return chunks or [text[:chunk_chars]]

    def similarity(self, left: np.ndarray, right: np.ndarray) -> float:
        score = compute_similarity(left, right)
        return max(0.0, min(1.0, score))

    def course_job_semantic_score(self, module_vectors: Sequence[np.ndarray], job_vector: np.ndarray) -> float:
        scores = sorted(
            (self.similarity(module_vector, job_vector) for module_vector in module_vectors),
            reverse=True,
        )
        if not scores:
            return 0.0
        top_scores = scores[:max(1, self.top_module_count)]
        return float(np.mean(top_scores))

    def skill_coverage_score(self, matched_skills: Sequence[str], job_skills: Sequence[str]) -> float:
        if not job_skills:
            return 0.0
        return len(set(matched_skills)) / max(1, len(set(job_skills)))

    def decision_tree_score(self, semantic_score: float, skill_score: float, confidence_score: float) -> float:
        """Interpretable ranking tree until enough reviewed outcomes exist for supervised training."""
        if semantic_score >= 0.78 and skill_score >= 0.45 and confidence_score >= 0.75:
            return 0.95
        if semantic_score >= 0.65 and (skill_score >= 0.35 or confidence_score >= 0.80):
            return 0.82
        if semantic_score >= 0.50 and skill_score >= 0.25:
            return 0.65
        if semantic_score >= 0.35 and skill_score >= 0.10 and confidence_score >= 0.85:
            return 0.50
        return max(0.0, min(1.0, (0.70 * semantic_score) + (0.30 * skill_score)))

    def final_score(
        self,
        semantic_score: float,
        matched_skills: Sequence[str],
        job_skills: Sequence[str],
        confidence_score: float = 0.0,
    ) -> ScoreBreakdown:
        skill_score = self.skill_coverage_score(matched_skills, job_skills)
        confidence_score = max(0.0, min(1.0, confidence_score))
        tree_score = self.decision_tree_score(semantic_score, skill_score, confidence_score)
        total_weight = max(
            0.01,
            self.semantic_weight + self.skill_weight + self.confidence_weight + self.decision_tree_weight,
        )
        final = (
            (self.semantic_weight * semantic_score)
            + (self.skill_weight * skill_score)
            + (self.confidence_weight * confidence_score)
            + (self.decision_tree_weight * tree_score)
        ) / total_weight
        return ScoreBreakdown(
            semantic_score=max(0.0, min(1.0, semantic_score)),
            skill_score=max(0.0, min(1.0, skill_score)),
            confidence_score=confidence_score,
            decision_tree_score=tree_score,
            final_score=max(0.0, min(1.0, final)),
            model="semantic_skill_confidence_decision_tree_ensemble",
        )
