from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .file_parsing import IgnoredUploadedFile, parse_uploaded_files
from .models import Course, Module


class ModuleFileUploadTests(TestCase):
    @patch("analysis.spacyskillextraction.SpacySkillExtractor")
    def test_module_create_extracts_skills_with_ner_helper(self, extractor_cls):
        extractor_cls.return_value.extract_entities.return_value = [
            {"skill": "data analysis", "id": "skill-data-analysis"}
        ]
        course = Course.objects.create(code="MBA201", name="MBA")

        response = self.client.post(
            reverse("module-create", args=[course.pk]),
            {
                "name": "Analytics",
                "content": "Use data analysis to support decisions.",
                "order": "1",
            },
        )

        self.assertRedirects(response, reverse("course-detail", args=[course.pk]))
        module = Module.objects.get(course=course)
        self.assertEqual(module.skills_extracted, ["data analysis"])
        self.assertEqual(module.skill_entities, [{"skill": "data analysis", "id": "skill-data-analysis"}])
        extractor_cls.return_value.extract_entities.assert_called_once_with(
            "Use data analysis to support decisions.",
            document_id=f"module-{module.pk}",
        )

    def test_text_file_upload_is_parsed_into_module_content(self):
        course = Course.objects.create(code="MBA101", name="MBA")
        uploaded_file = SimpleUploadedFile(
            "syllabus.txt",
            b"Strategy\nLeadership\nAnalytics",
            content_type="text/plain",
        )

        response = self.client.post(
            reverse("module-create", args=[course.pk]),
            {
                "name": "Business Strategy",
                "content": "Pasted overview",
                "order": "1",
                "content_files": [uploaded_file],
            },
        )

        self.assertRedirects(response, reverse("course-detail", args=[course.pk]))
        module = Module.objects.get(course=course)
        self.assertIn("Pasted overview", module.content)
        self.assertIn("--- syllabus.txt ---", module.content)
        self.assertIn("Leadership", module.content)

    def test_upload_parser_rejects_unsupported_file_type(self):
        uploaded_file = SimpleUploadedFile(
            "syllabus.csv",
            b"Strategy,Leadership",
            content_type="text/csv",
        )

        with self.assertRaisesMessage(Exception, "unsupported file type"):
            parse_uploaded_files([uploaded_file])

    def test_upload_parser_ignores_invalid_docx_files(self):
        broken_docx = SimpleUploadedFile(
            "Ethics Assignment.docx",
            b"this is not a zipped office file",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        syllabus = SimpleUploadedFile("syllabus.txt", b"Ethics and leadership", content_type="text/plain")

        result = parse_uploaded_files([broken_docx, syllabus])

        self.assertEqual(result.ignored_count, 1)
        self.assertEqual(result.ignored_files, ["Ethics Assignment.docx"])
        self.assertIn("Ethics and leadership", result.text)

    @patch("courses.file_parsing.parse_uploaded_file")
    def test_upload_parser_ignores_password_protected_pdfs(self, parse_file):
        locked_pdf = SimpleUploadedFile("Unit 2 - Asessment (1).pdf", b"locked", content_type="application/pdf")
        syllabus = SimpleUploadedFile("syllabus.txt", b"Leadership", content_type="text/plain")
        parse_file.side_effect = [IgnoredUploadedFile(locked_pdf.name), "Leadership"]

        result = parse_uploaded_files([locked_pdf, syllabus])

        self.assertEqual(result.ignored_count, 1)
        self.assertEqual(result.ignored_files, ["Unit 2 - Asessment (1).pdf"])
        self.assertIn("--- syllabus.txt ---", result.text)
        self.assertIn("Leadership", result.text)

    def test_module_skill_enhancement_adds_manual_skill_entities(self):
        course = Course.objects.create(code="MBA202", name="MBA")
        module = Module.objects.create(
            course=course,
            name="Analytics",
            content="Business analytics and reporting",
            skills_extracted=["data analysis"],
            skill_entities=[{"skill": "data analysis", "id": "skill-data-analysis"}],
        )

        response = self.client.post(
            reverse("module-skill-enhance", args=[module.pk]),
            {"skills": "Power BI, stakeholder management\nData Analysis"},
        )

        self.assertRedirects(response, reverse("course-detail", args=[course.pk]))
        module.refresh_from_db()
        self.assertEqual(module.skills_extracted, ["data analysis", "power bi", "stakeholder management"])
        manual_entities = [
            entity for entity in module.skill_entities
            if entity.get("source") == "manual_enhancement"
        ]
        self.assertEqual({entity["skill"] for entity in manual_entities}, {"power bi", "stakeholder management"})
        self.assertTrue(all(entity["label_status"] == "reviewed" for entity in manual_entities))


class CourseFilterTests(TestCase):
    @patch("analysis.spacyskillextraction.SpacySkillExtractor")
    def test_course_list_backfills_and_displays_module_skills(self, extractor_cls):
        extractor_cls.return_value.extract_entities.return_value = [
            {"skill": "leadership", "id": "skill-leadership"},
            {"skill": "strategy", "id": "skill-strategy"},
        ]
        course = Course.objects.create(code="MBA301", name="MBA")
        module = Module.objects.create(
            course=course,
            name="Strategy",
            content="Leadership and strategy",
        )

        response = self.client.get(reverse("course-list"))

        self.assertContains(response, "2 skills")
        self.assertContains(response, "leadership")
        self.assertContains(response, "strategy")
        module.refresh_from_db()
        self.assertEqual(module.skills_extracted, ["leadership", "strategy"])

    def test_course_detail_exposes_skill_enhancement_form(self):
        course = Course.objects.create(code="MBA302", name="MBA")
        module = Module.objects.create(
            course=course,
            name="Strategy",
            content="Leadership and strategy",
            skills_extracted=["leadership"],
        )

        response = self.client.get(reverse("course-detail", args=[course.pk]))

        self.assertContains(response, "Enhance extracted skills")
        self.assertContains(response, "Clean extracted skills")
        self.assertContains(response, reverse("course-skill-audit", args=[course.pk]))
        self.assertContains(response, reverse("module-skill-enhance", args=[module.pk]))

    def test_course_skill_audit_lists_module_skills(self):
        course = Course.objects.create(code="MBA303", name="MBA")
        Module.objects.create(
            course=course,
            name="Strategy",
            content="Leadership and strategy",
            skills_extracted=["leadership", "not a skill"],
            skill_entities=[
                {"skill": "leadership", "source": "ner", "confidence": 0.96, "skill_type": "soft"},
                {"skill": "not a skill", "source": "bert-ner", "confidence": 0.66, "skill_type": "domain"},
            ],
        )

        response = self.client.get(reverse("course-skill-audit", args=[course.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course Skill Cleanup")
        self.assertContains(response, "2")
        self.assertContains(response, "not a skill")
        self.assertContains(response, reverse("module-skill-delete", args=[course.modules.first().pk]))

    def test_course_skill_audit_shows_full_evidence_context(self):
        course = Course.objects.create(code="MBA305", name="MBA")
        content = (
            "Overview paragraph.\n\n"
            "Students evaluate stakeholder mapping, executive communication, and "
            "scenario planning in complex strategy projects.\n\n"
            "Assessment paragraph."
        )
        Module.objects.create(
            course=course,
            name="Strategy Evidence",
            content=content,
            skills_extracted=["scenario planning"],
            skill_entities=[{
                "id": "skill-scenario-planning",
                "chunk_id": "chunk-scenario-planning",
                "skill": "scenario planning",
                "source": "phrase_matcher",
                "text": "scenario planning",
                "start": content.index("scenario planning"),
                "end": content.index("scenario planning") + len("scenario planning"),
                "skill_type": "business",
            }],
        )

        response = self.client.get(reverse("course-skill-audit", args=[course.pk]))

        self.assertContains(response, "Students evaluate stakeholder mapping")
        self.assertContains(response, "complex strategy projects")
        self.assertContains(response, "name=\"skill_type\"")

    def test_course_skill_audit_updates_skill_type_and_marks_reviewed(self):
        course = Course.objects.create(code="MBA306", name="MBA")
        module = Module.objects.create(
            course=course,
            name="Analytics",
            content="Scenario planning supports strategic decisions.",
            skills_extracted=["scenario planning"],
            skill_entities=[{
                "id": "skill-scenario-planning",
                "chunk_id": "chunk-scenario-planning",
                "skill": "scenario planning",
                "label": "SKILL",
                "tier": "candidate",
                "skill_type": "domain",
                "source": "phrase_matcher",
            }],
        )

        response = self.client.post(reverse("course-skill-audit", args=[course.pk]), {
            "module_id": module.pk,
            "original_skill": "scenario planning",
            "entity_id": "skill-scenario-planning",
            "chunk_id": "chunk-scenario-planning",
            "skill": "scenario planning",
            "label": "SKILL",
            "tier": "method",
            "skill_type": "business",
        })

        self.assertRedirects(response, reverse("course-skill-audit", args=[course.pk]))
        module.refresh_from_db()
        self.assertEqual(module.skill_entities[0]["skill_type"], "business")
        self.assertEqual(module.skill_entities[0]["tier"], "method")
        self.assertEqual(module.skill_entities[0]["label_status"], "reviewed")

    def test_module_skill_delete_removes_skill_and_entity(self):
        course = Course.objects.create(code="MBA304", name="MBA")
        module = Module.objects.create(
            course=course,
            name="Strategy",
            content="Leadership and strategy",
            skills_extracted=["leadership", "not a skill"],
            skill_entities=[
                {"skill": "leadership", "source": "ner"},
                {"skill": "not a skill", "source": "bert-ner"},
            ],
        )

        response = self.client.post(
            reverse("module-skill-delete", args=[module.pk]),
            {"skill": "not a skill", "next": "audit"},
        )

        self.assertRedirects(response, reverse("course-skill-audit", args=[course.pk]))
        module.refresh_from_db()
        self.assertEqual(module.skills_extracted, ["leadership"])
        self.assertEqual(module.skill_entities, [{"skill": "leadership", "source": "ner"}])

    def test_course_list_filters_by_module_university_and_country(self):
        matching_course = Course.objects.create(code="MBA101", name="MBA")
        other_course = Course.objects.create(code="CS101", name="Computer Science")
        matching_course.university_name = "Johannesburg Business School"
        matching_course.country = "South Africa"
        matching_course.save(update_fields=["university_name", "country"])
        other_course.university_name = "Other School"
        other_course.country = "Namibia"
        other_course.save(update_fields=["university_name", "country"])
        Module.objects.create(
            course=matching_course,
            name="Strategy",
            content="Leadership strategy",
        )
        Module.objects.create(
            course=other_course,
            name="Programming",
            content="Python programming",
            university_name="Other School",
            country="Namibia",
        )

        response = self.client.get(
            reverse("course-list"),
            {"university": "Johannesburg Business School", "country": "South Africa"},
        )

        self.assertContains(response, "MBA")
        self.assertContains(response, "Johannesburg Business School")
        self.assertNotContains(response, "Computer Science")

    def test_module_create_inherits_course_university_and_country(self):
        course = Course.objects.create(
            code="MBA102",
            name="MBA",
            university_name="University of Johannesburg",
            country="South Africa",
        )

        response = self.client.post(
            reverse("module-create", args=[course.pk]),
            {
                "name": "Leadership",
                "content": "Leadership and strategy",
                "order": "1",
            },
        )

        self.assertRedirects(response, reverse("course-detail", args=[course.pk]))
        module = Module.objects.get(course=course)
        self.assertEqual(module.university_name, "University of Johannesburg")
        self.assertEqual(module.country, "South Africa")
