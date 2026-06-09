from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import override_settings
from django.test import TestCase

from analysis.course_skill_ner import ensure_course_skill_ner_model, model_needs_training, training_fingerprint
from analysis.gemini_cleaning import _limit_prompt_text
from analysis.nlp_pipeline import BUSINESS_SKILL_EXCLUDED_TERMS, SKILL_KEYWORDS, extract_skills
from analysis.semantic_similarity import SemanticSimilarityService
from analysis.services import _matched_skill_confidence
from analysis.spacyskillextraction import SpacySkillExtractor, classify_skill_text
from courses.models import Course, Module


class SpacySkillExtractorTests(TestCase):
    def test_extract_entities_returns_stable_skill_ids(self):
        extractor = SpacySkillExtractor()

        entities = extractor.extract_entities("This role needs project management and Power BI dashboards.")

        by_skill = {entity["skill"]: entity for entity in entities}
        self.assertIn("project management", by_skill)
        self.assertEqual(by_skill["project management"]["id"], "skill-project-management")
        self.assertEqual(by_skill["project management"]["label"], "SKILL")
        self.assertIn(by_skill["project management"]["source"], {"ner", "phrase_matcher", "regex"})

    def test_programming_language_skills_are_excluded_from_business_extractor(self):
        extractor = SpacySkillExtractor()

        entities = extractor.extract_entities("Experience with c#, c++, Go, Golang, and Go programming language is useful.")
        skills = {entity["skill"] for entity in entities}

        self.assertNotIn("c#", skills)
        self.assertNotIn("c++", skills)
        self.assertNotIn("go", skills)
        self.assertNotIn("golang", skills)
        self.assertNotIn("go programming language", skills)

    def test_auto_classifies_skill_type_and_tier(self):
        self.assertEqual(classify_skill_text("c#")["skill_type"], "technical")
        self.assertEqual(classify_skill_text("c#")["tier"], "tool")
        self.assertEqual(classify_skill_text("leadership")["skill_type"], "soft")
        self.assertEqual(classify_skill_text("leadership")["tier"], "transferable")
        self.assertEqual(classify_skill_text("project management")["skill_type"], "business")
        self.assertEqual(classify_skill_text("project management")["tier"], "method")

    def test_regex_fallback_does_not_extract_c_plus_plus(self):
        self.assertNotIn("c++", extract_skills("Experience with c++ and systems programming."))

    def test_business_keyword_list_excludes_programming_stack_terms(self):
        self.assertFalse(BUSINESS_SKILL_EXCLUDED_TERMS & set(SKILL_KEYWORDS))

    def test_regex_entities_are_disabled_by_default(self):
        extractor = SpacySkillExtractor()

        entities = extractor.extract_entities("This course introduces computer science and programming.")

        self.assertNotIn("regex", {entity["source"] for entity in entities})

    def test_regex_only_fallback_requires_explicit_setting(self):
        with override_settings(SKILL_REGEX_FALLBACK_ENABLED=False):
            with patch.object(SpacySkillExtractor, "_load_spacy", lambda self: None):
                extractor = SpacySkillExtractor()

        self.assertEqual(extractor.extract_entities("Python and programming"), [])

    def test_low_confidence_bert_entities_are_ignored(self):
        with override_settings(BERT_SKILL_NER_MIN_CONFIDENCE=0.65):
            with patch.object(SpacySkillExtractor, "_load_bert_ner", lambda self: None):
                extractor = SpacySkillExtractor()
                extractor._bert_ner = lambda text: [
                    {
                        "entity_group": "SKILL",
                        "word": "requires",
                        "start": 10,
                        "end": 18,
                        "score": 0.2,
                    }
                ]

                entities = extractor.extract_entities("This role requires reliable execution.")

        self.assertNotIn("requires", {entity["skill"] for entity in entities})


class CourseSkillNerTrainingTests(TestCase):
    def test_model_needs_training_when_model_is_missing(self):
        with TemporaryDirectory() as tmpdir:
            self.assertTrue(model_needs_training(Path(tmpdir) / "missing-model"))

    def test_auto_training_skips_when_existing_model_is_current(self):
        course = Course.objects.create(code="MBA999", name="MBA")
        Module.objects.create(
            course=course,
            name="Analytics",
            content="Data analysis",
            skills_extracted=["data analysis"],
        )
        with TemporaryDirectory() as tmpdir:
            model_dir = Path(tmpdir) / "course_skill_ner"
            model_dir.mkdir()
            (model_dir / "meta.json").write_text("{}", encoding="utf-8")
            (model_dir / "training-fingerprint.txt").write_text(training_fingerprint(), encoding="utf-8")

            with override_settings(COURSE_SKILL_NER_MODEL_PATH=str(model_dir)):
                with patch("analysis.course_skill_ner.train_course_skill_ner") as train:
                    result = ensure_course_skill_ner_model()

            train.assert_not_called()
            self.assertFalse(result["trained"])
            self.assertIn("up to date", result["reason"])


class EnsembleScoringTests(TestCase):
    def scorer(self):
        scorer = SemanticSimilarityService.__new__(SemanticSimilarityService)
        scorer.semantic_weight = 0.55
        scorer.skill_weight = 0.20
        scorer.confidence_weight = 0.15
        scorer.decision_tree_weight = 0.10
        return scorer

    def test_decision_tree_rewards_strong_semantic_skill_and_confidence_evidence(self):
        scorer = self.scorer()

        weak = scorer.final_score(
            0.55,
            ["python"],
            ["python", "sql", "power bi", "communication"],
            confidence_score=0.35,
        )
        strong = scorer.final_score(
            0.78,
            ["python", "sql"],
            ["python", "sql", "power bi", "communication"],
            confidence_score=0.88,
        )

        self.assertGreater(strong.final_score, weak.final_score)
        self.assertGreater(strong.decision_tree_score, weak.decision_tree_score)
        self.assertEqual(strong.model, "semantic_skill_confidence_decision_tree_ensemble")

    def test_matched_skill_confidence_uses_course_and_job_evidence(self):
        score = _matched_skill_confidence(
            ["python", "sql"],
            [{"skill": "python", "confidence": 0.8}, {"skill": "sql", "confidence": 0.6}],
            [{"skill": "python", "confidence": 0.9}, {"skill": "sql", "confidence": 0.7}],
        )

        self.assertAlmostEqual(score, 0.75)

    def test_semantic_vectorize_chunks_long_ollama_inputs(self):
        class FakeOllama:
            def __init__(self):
                self.prompts = []

            def embeddings(self, model, prompt):
                self.prompts.append(prompt)
                return {"embedding": [float(len(prompt) or 1), 1.0, 1.0]}

        scorer = self.scorer()
        scorer.backend = "ollama"
        scorer.model = "nomic-embed-text"
        scorer._ollama = FakeOllama()
        scorer.embed_chunk_chars = 1000
        scorer.embed_max_chunks = 4

        vector = scorer.vectorize("strategy " * 900)

        self.assertGreater(len(scorer._ollama.prompts), 1)
        self.assertLessEqual(max(len(prompt) for prompt in scorer._ollama.prompts), 1000)
        self.assertAlmostEqual(float((vector ** 2).sum()), 1.0, places=5)

    def test_semantic_vectorize_bypasses_context_length_failures(self):
        class FailingOllama:
            def embeddings(self, model, prompt):
                raise RuntimeError("input length exceeds the context length (status code: 500)")

        messages = []
        scorer = self.scorer()
        scorer.backend = "ollama"
        scorer.model = "nomic-embed-text"
        scorer.embedding_dim = 3
        scorer.embedding_failures = 0
        scorer._ollama = FailingOllama()
        scorer._progress_callback = messages.append

        vector = scorer.vectorize("oversized course material")

        self.assertEqual(vector.tolist(), [0.0, 0.0, 0.0])
        self.assertEqual(scorer.embedding_failures, 1)
        self.assertTrue(any("neutral semantic vector" in message for message in messages))


class GeminiCleaningPromptTests(TestCase):
    @override_settings(GEMINI_PROMPT_MAX_CHARS=4000)
    def test_prompt_text_is_limited_to_context_window(self):
        text = "A" * 5000 + " important tail evidence"

        limited = _limit_prompt_text(text)

        self.assertLessEqual(len(limited), 4100)
        self.assertIn("characters omitted", limited)
        self.assertIn("important tail evidence", limited)
