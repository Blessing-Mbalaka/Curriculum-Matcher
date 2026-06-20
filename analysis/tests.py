from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import override_settings
from django.test import TestCase

from analysis.course_skill_ner import (
    collect_skill_ner_training_examples,
    ensure_course_skill_ner_model,
    model_needs_training,
    training_fingerprint,
)
from analysis.gemini_cleaning import _limit_prompt_text
from analysis.nlp_pipeline import BUSINESS_SKILL_EXCLUDED_TERMS, SKILL_KEYWORDS, extract_skills
from analysis.semantic_similarity import SemanticSimilarityService
from analysis.services import _matched_skill_confidence
from analysis.spacyskillextraction import SpacySkillExtractor, classify_skill_text
from analysis.verification import (
    normalize_verification_response,
    save_candidate_skill_entities,
    suspicious_module_records,
    verify_database,
)
from courses.models import Course, Module
from jobs.models import JobAdvert


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

    def test_dynamic_lexicon_extracts_stored_dataset_skills(self):
        course = Course.objects.create(code="MBA401", name="MBA")
        Module.objects.create(
            course=course,
            name="Innovation",
            content="Human centred innovation",
            skills_extracted=["human centred design"],
            skill_entities=[{
                "skill": "human centred design",
                "label": "SKILL",
                "label_status": "reviewed",
            }],
        )

        with override_settings(DYNAMIC_SKILL_LEXICON_ENABLED=True):
            with patch.object(SpacySkillExtractor, "_load_bert_ner", lambda self: None):
                extractor = SpacySkillExtractor()

        entities = extractor.extract_entities("The module develops human centred design for service innovation.")

        self.assertIn("human centred design", {entity["skill"] for entity in entities})
        self.assertIn("human centred design", extractor.dynamic_skill_terms)

    def test_dynamic_lexicon_extracts_csv_seed_skills(self):
        with TemporaryDirectory() as tmpdir:
            seed_path = Path(tmpdir) / "skills.csv"
            seed_path.write_text("skill\nscenario planning\n", encoding="utf-8")

            with override_settings(
                DYNAMIC_SKILL_LEXICON_ENABLED=True,
                DYNAMIC_SKILL_LEXICON_CSV_PATH=str(seed_path),
            ):
                with patch.object(SpacySkillExtractor, "_load_bert_ner", lambda self: None):
                    extractor = SpacySkillExtractor()

        entities = extractor.extract_entities("Students practice scenario planning under uncertainty.")

        self.assertIn("scenario planning", {entity["skill"] for entity in entities})


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


class SkillVerificationTests(TestCase):
    def test_suspicious_module_records_flags_long_one_skill_content(self):
        course = Course.objects.create(code="MBA501", name="MBA")
        Module.objects.create(
            course=course,
            name="Operations Analytics",
            content=(
                "Students use forecasting, operations management, stakeholder communication, "
                "budgeting, process improvement, data analysis, and scenario planning in "
                "a multi-week business simulation."
            ),
            skills_extracted=["forecasting"],
        )

        records = suspicious_module_records(limit=5, min_text_chars=80, suspicious_skill_count=1)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["skill_count"], 1)
        self.assertIn("Long text has only 1 extracted skill", records[0]["warnings"][0])

    def test_verification_response_normalizes_llm_json(self):
        parsed = normalize_verification_response({
            "status": "needs_review",
            "suspicious": True,
            "suggested_skills": "Data Analysis, Scenario Planning\nSAP_Financial_Accounting",
            "false_positive_stored_skills": ["not a skill", "not a skill"],
            "notes": "Stored extraction looks thin.",
        })

        self.assertEqual(parsed["suggested_skills"], ["data analysis", "scenario planning", "sap financial accounting"])
        self.assertEqual(parsed["false_positive_stored_skills"], ["not a skill"])
        self.assertTrue(parsed["suspicious"])

    def test_verify_database_writes_reports_without_llm(self):
        course = Course.objects.create(code="MBA502", name="MBA")
        Module.objects.create(
            course=course,
            name="Strategy",
            content="Strategy leadership analytics communication governance " * 20,
            skills_extracted=["strategy"],
        )

        with TemporaryDirectory() as tmpdir:
            result = verify_database(
                max_jobs=0,
                max_modules=5,
                min_text_chars=100,
                suspicious_skill_count=1,
                use_llm=False,
                output_dir=tmpdir,
            )

            self.assertFalse(result["llm_available"])
            self.assertEqual(result["summary"]["modules_checked"], 1)
            self.assertTrue(Path(result["paths"]["json"]).exists())
            self.assertTrue(Path(result["paths"]["markdown"]).exists())

    def test_save_candidate_skill_entities_adds_review_queue_without_extracted_skills(self):
        job = JobAdvert.objects.create(
            title="SAP Finance Analyst",
            description="SAP financial accounting, treasury solutions, workshops, and reporting.",
            skills_extracted=["accounting"],
        )
        records = [{
            "source_type": "job",
            "source_id": job.id,
            "llm_verification": {
                "status": "needs_review",
                "suggested_skills": ["sap financial accounting", "treasury solutions"],
                "notes": "Stored extraction is incomplete.",
            },
        }]

        saved = save_candidate_skill_entities(records)

        job.refresh_from_db()
        self.assertEqual(saved, 2)
        self.assertEqual(job.skills_extracted, ["accounting"])
        self.assertEqual({entity["skill"] for entity in job.skill_entities}, {
            "accounting",
            "sap financial accounting",
            "treasury solutions",
        })
        candidate_skills = {
            entity["skill"]
            for entity in job.skill_entities
            if entity["label_status"] == "candidate"
        }
        self.assertEqual(candidate_skills, {"sap financial accounting", "treasury solutions"})

    def test_reviewed_job_skill_entities_feed_bert_training_examples(self):
        JobAdvert.objects.create(
            title="SAP Finance Analyst",
            description="The analyst uses SAP financial accounting for treasury reporting.",
            skill_entities=[{
                "skill": "sap financial accounting",
                "label": "SKILL",
                "label_status": "reviewed",
            }],
        )

        examples, skipped = collect_skill_ner_training_examples(reviewed_only=True, include_jobs=True)

        self.assertEqual(skipped, 0)
        self.assertEqual(len(examples), 1)
        text, annotations = examples[0]
        self.assertIn("SAP financial accounting", text)
        self.assertEqual(annotations["entities"][0][2], "SKILL")
