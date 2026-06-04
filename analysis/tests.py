from django.test import TestCase

from analysis.spacyskillextraction import SpacySkillExtractor, classify_skill_text


class SpacySkillExtractorTests(TestCase):
    def test_extract_entities_returns_stable_skill_ids(self):
        extractor = SpacySkillExtractor()

        entities = extractor.extract_entities("This role needs project management and Power BI dashboards.")

        by_skill = {entity["skill"]: entity for entity in entities}
        self.assertIn("project management", by_skill)
        self.assertEqual(by_skill["project management"]["id"], "skill-project-management")
        self.assertEqual(by_skill["project management"]["label"], "SKILL")
        self.assertIn(by_skill["project management"]["source"], {"ner", "phrase_matcher", "regex"})

    def test_punctuation_skills_do_not_collapse_to_same_id(self):
        extractor = SpacySkillExtractor()

        entities = extractor.extract_entities("Experience with c# and c++ is useful.")
        by_skill = {entity["skill"]: entity["id"] for entity in entities}

        self.assertNotEqual(by_skill["c#"], by_skill["c++"])

    def test_auto_classifies_skill_type_and_tier(self):
        self.assertEqual(classify_skill_text("c#")["skill_type"], "technical")
        self.assertEqual(classify_skill_text("c#")["tier"], "tool")
        self.assertEqual(classify_skill_text("leadership")["skill_type"], "soft")
        self.assertEqual(classify_skill_text("leadership")["tier"], "transferable")
        self.assertEqual(classify_skill_text("project management")["skill_type"], "business")
        self.assertEqual(classify_skill_text("project management")["tier"], "method")
