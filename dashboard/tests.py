from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from analysis.models import AnalysisRun, GapResult, SkillAlias, SkillMatrix
from analysis.models import TaskRecord
from courses.models import Course
from courses.models import Module
from dashboard.views import build_results_visual_data, current_extracted_skill_rows, recommendation_skill_insight, refine_skill_rows_for_business
from jobs.models import JobAdvert


class DashboardVisualDataTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(code="MBA101", name="MBA")
        self.job = JobAdvert.objects.create(title="Strategy Manager", description="Strategy and leadership")

    def test_metrics_keep_previous_visual_data_when_newest_run_is_empty(self):
        older_run = AnalysisRun.objects.create(name="Run with data", status="done")
        GapResult.objects.create(
            run=older_run,
            course=self.course,
            job=self.job,
            similarity_score=0.82,
            matched_skills=["strategy"],
            missing_skills=[],
        )
        SkillMatrix.objects.create(run=older_run, source="jobs", skill="strategy", frequency=3)
        AnalysisRun.objects.create(name="Newest empty run", status="running")

        response = self.client.get(reverse("dashboard-metrics"))
        data = response.json()

        self.assertTrue(data["has_visual_data"])
        self.assertEqual(data["visual_run"]["id"], older_run.id)
        self.assertEqual(data["average_score"], 82.0)
        self.assertEqual(sum(data["score_buckets"]), 1)

    def test_dashboard_can_view_historic_run_data(self):
        old_run = AnalysisRun.objects.create(name="Historic run", status="done")
        new_run = AnalysisRun.objects.create(name="Latest run", status="done")
        GapResult.objects.create(
            run=old_run,
            course=self.course,
            job=self.job,
            similarity_score=0.42,
            matched_skills=["strategy"],
            missing_skills=["analytics"],
        )
        GapResult.objects.create(
            run=new_run,
            course=self.course,
            job=self.job,
            similarity_score=0.91,
            matched_skills=["strategy", "analytics"],
            missing_skills=[],
        )

        page = self.client.get(reverse("home"), {"run": old_run.id})
        metrics = self.client.get(reverse("dashboard-metrics"), {"run": old_run.id}).json()
        network = self.client.get(reverse("similarity-network"), {"run": old_run.id}).json()

        self.assertContains(page, "Historic Run Viewer")
        self.assertContains(page, "id=\"dashboardRunSelect\"")
        self.assertContains(page, f"?run={old_run.id}")
        self.assertContains(page, "Historic run")
        self.assertContains(page, "42.0%")
        self.assertEqual(metrics["visual_run"]["id"], old_run.id)
        self.assertEqual(metrics["average_score"], 42.0)
        self.assertEqual(network["run_id"], old_run.id)

    def test_metrics_skill_chart_uses_actual_extracted_skill_names(self):
        run = AnalysisRun.objects.create(name="Run with skill matrix", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.82,
            matched_skills=["data analysis"],
            missing_skills=["sql"],
        )
        SkillMatrix.objects.create(run=run, source="jobs", skill="c++", frequency=99)
        SkillMatrix.objects.create(run=run, source="courses", skill="c++", frequency=99)
        self.job.skills_extracted = ["data analysis"]
        self.job.save(update_fields=["skills_extracted"])
        Module.objects.create(
            course=self.course,
            name="Analytics",
            content="Data analysis for managers",
            skills_extracted=["data analysis"],
        )

        response = self.client.get(reverse("dashboard-metrics"))
        data = response.json()

        self.assertEqual(data["job_skills"][0]["skill"], "data analysis")
        self.assertEqual(data["course_skills"][0]["skill"], "data analysis")
        self.assertNotIn("c++", {row["skill"] for row in data["job_skills"]})
        self.assertNotIn("c++", {row["skill"] for row in data["course_skills"]})
        self.assertNotEqual(data["job_skills"][0]["skill"], "business analytics")

    def test_dashboard_skill_metrics_filter_programming_jargon(self):
        self.job.skill_entities = [
            {"skill": "python", "source": "ner"},
            {"skill": "r", "source": "ner"},
            {"skill": "leadership", "source": "ner"},
        ]
        self.job.skills_extracted = ["python", "r", "leadership"]
        self.job.save(update_fields=["skill_entities", "skills_extracted"])
        Module.objects.create(
            course=self.course,
            name="Strategy",
            content="Leadership",
            skill_entities=[
                {"skill": "software engineering", "source": "phrase_matcher"},
                {"skill": "communication", "source": "ner"},
            ],
            skills_extracted=["software engineering", "communication"],
        )

        job_skills = {row["skill"] for row in current_extracted_skill_rows(JobAdvert)}
        course_skills = {row["skill"] for row in current_extracted_skill_rows(Module)}

        self.assertEqual(job_skills, {"leadership"})
        self.assertEqual(course_skills, {"communication"})

    def test_network_keeps_previous_visual_data_and_returns_static_positions(self):
        older_run = AnalysisRun.objects.create(name="Run with data", status="done")
        GapResult.objects.create(
            run=older_run,
            course=self.course,
            job=self.job,
            similarity_score=0.82,
            matched_skills=["strategy"],
            missing_skills=[],
        )
        AnalysisRun.objects.create(name="Newest empty run", status="running")

        response = self.client.get(reverse("similarity-network"))
        data = response.json()

        self.assertTrue(data["has_visual_data"])
        self.assertEqual(data["run_id"], older_run.id)
        self.assertTrue(data["nodes"])
        self.assertIn("x", data["nodes"][0])
        self.assertIn("y", data["nodes"][0])

    def test_network_filters_by_school_and_job(self):
        self.course.university_name = "University of Johannesburg"
        self.course.save(update_fields=["university_name"])
        other_course = Course.objects.create(code="OTH101", name="Other MBA", university_name="Other School")
        other_job = JobAdvert.objects.create(title="Operations Manager", description="Operations")
        run = AnalysisRun.objects.create(name="Filtered network", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.82,
            matched_skills=["strategy"],
            missing_skills=[],
        )
        GapResult.objects.create(
            run=run,
            course=other_course,
            job=other_job,
            similarity_score=0.61,
            matched_skills=["operations"],
            missing_skills=[],
        )

        response = self.client.get(
            reverse("similarity-network"),
            {"school": "University of Johannesburg", "job": self.job.id},
        )
        data = response.json()

        self.assertTrue(data["has_visual_data"])
        node_labels = {node["label"] for node in data["nodes"]}
        self.assertIn("MBA101", node_labels)
        self.assertIn("Strategy Manager", node_labels)
        self.assertNotIn("OTH101", node_labels)
        self.assertNotIn("Operations Manager", node_labels)

    def test_network_cluster_mode_returns_skill_entity_nodes(self):
        self.job.skill_entities = [{
            "id": "skill-strategy",
            "skill": "strategy",
            "source": "ner",
            "confidence": 0.96,
            "mention_count": 2,
        }]
        self.job.skills_extracted = ["strategy"]
        self.job.save(update_fields=["skill_entities", "skills_extracted"])
        run = AnalysisRun.objects.create(name="Cluster network", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.82,
            matched_skills=["strategy"],
            missing_skills=[],
        )

        response = self.client.get(reverse("similarity-network"), {"cluster": "1"})
        data = response.json()

        self.assertTrue(data["cluster"])
        skill_nodes = [node for node in data["nodes"] if node["group"] == "skill"]
        self.assertEqual(skill_nodes[0]["entity_id"], "skill-strategy")
        self.assertEqual(skill_nodes[0]["skill"], "strategy")
        self.assertTrue(any(edge["from"] == f"job-{self.job.id}" and edge["to"] == "skill-strategy" for edge in data["edges"]))

    def test_queue_analysis_ajax_creates_database_only_task_record(self):
        response = self.client.post(
            reverse("run-analysis"),
            {"run_name": "Run 99"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status_url", data)
        self.assertTrue(TaskRecord.objects.filter(run_name="Run 99").exists())

    def test_smoke_analysis_ajax_accepts_three_job_limit(self):
        response = self.client.post(
            reverse("run-analysis"),
            {"run_name": "Run 100", "max_jobs": "3"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["max_jobs"], 3)
        self.assertTrue(TaskRecord.objects.filter(run_name="Smoke Run 100").exists())

    def test_dashboard_renders_course_to_job_match_tiles(self):
        self.course.university_name = "University of Johannesburg"
        self.course.save(update_fields=["university_name"])
        self.job.skills_extracted = ["strategy", "analytics", "communication"]
        self.job.save(update_fields=["skills_extracted"])
        Module.objects.create(
            course=self.course,
            name="Strategy Evidence",
            content="Strategy leadership and communication",
            skills_extracted=["strategy", "leadership", "communication"],
        )
        run = AnalysisRun.objects.create(name="Run with matches", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.78,
            matched_skills=["strategy"],
            missing_skills=["analytics"],
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Best Course-to-Job Matches")
        self.assertContains(response, "Historic Run Viewer")
        self.assertContains(response, "Select historic analysis run")
        self.assertContains(response, "Open Gap Results")
        self.assertContains(response, "Export Report")
        self.assertContains(response, "Download Report Visuals")
        self.assertContains(response, "Download Visual CSV")
        self.assertContains(response, reverse("dashboard-visual-export"))
        self.assertContains(response, "id=\"voiceNarrationToggle\"")
        self.assertContains(response, "curriculumMatchChartVoiceEnabled")
        self.assertContains(response, "SpeechSynthesisUtterance")
        self.assertContains(response, "querySelector('.graph-insight-body')")
        self.assertContains(response, "match-tile")
        self.assertContains(response, "const demandLabels = data.job_skills")
        self.assertContains(response, "const curriculumLabels = data.course_skills")
        self.assertContains(response, "...demandLabels.slice(0, 5)")
        self.assertContains(response, "...curriculumLabels.slice(0, 5)")
        self.assertContains(response, "Courses: no matching course evidence for this skill")
        self.assertContains(response, "Course to job advert")
        self.assertContains(response, "Filter network by school")
        self.assertContains(response, "University of Johannesburg")
        self.assertContains(response, "Filter network by job advert")
        self.assertContains(response, "Strategy Manager")
        self.assertContains(response, "id=\"network3dBtn\"")
        self.assertContains(response, "id=\"networkCrosstabBtn\"")
        self.assertContains(response, "Cross-tab")
        self.assertContains(response, "plotly-2.35.2.min.js")
        self.assertContains(response, "Course-to-Job Similarity Cross-tab")
        self.assertContains(response, "type:'heatmap'")
        self.assertContains(response, "3d-force-graph")
        self.assertContains(response, "id=\"similarityNetwork3d\"")
        self.assertContains(response, "id=\"similarityCrosstab\"")
        self.assertContains(response, "network-canvas-wrap is-3d")
        self.assertContains(response, "Cosine similarity:")
        self.assertContains(response, "Job evidence skills")
        self.assertContains(response, "Course evidence skills")
        self.assertContains(response, "analytics")
        self.assertContains(response, "communication")

    def test_data_export_page_renders_skill_evidence_and_exports_csv(self):
        self.job.category = "Business Strategy"
        self.job.date_posted = date(2025, 5, 20)
        self.job.skill_entities = [{
            "id": "skill-strategy",
            "chunk_id": "chunk-job-strategy",
            "skill": "strategy",
            "label": "SKILL",
            "tier": "explicit",
            "skill_type": "business",
            "source": "ner",
            "confidence": 0.96,
            "mention_count": 1,
        }]
        self.job.save(update_fields=["category", "date_posted", "skill_entities"])
        Module.objects.create(
            course=self.course,
            name="Strategy module",
            content="Leadership and strategy",
            skills_extracted=["leadership"],
        )
        Module.objects.create(
            course=self.course,
            name="Analytics module",
            content="Data analysis supports decisions.",
            skill_entities=[{
                "id": "skill-data-analysis",
                "chunk_id": "chunk-module-data-analysis",
                "skill": "data analysis",
                "label": "SKILL",
                "tier": "capability",
                "skill_type": "technical",
                "source": "ner",
                "confidence": 0.96,
                "mention_count": 1,
                "text": "Data analysis",
                "start": 0,
                "end": 13,
                "label_status": "machine",
            }],
            skills_extracted=["data analysis"],
        )

        response = self.client.get(reverse("data-export"))
        csv_response = self.client.get(reverse("skill-entity-export"))
        course_dataset_response = self.client.get(reverse("course-skill-training-export"))

        self.assertContains(response, "Data Export")
        self.assertContains(response, "Export Visual CSV")
        self.assertContains(response, "Export Course NER Dataset")
        self.assertContains(response, "Check Training Dataset")
        self.assertContains(response, "data-training-readiness-button")
        self.assertContains(response, "Course Skill NER Training Dataset")
        self.assertContains(response, "Download Cleaned Skill Data")
        self.assertContains(response, reverse("cleaned-skill-csv-download", args=["courses"]))
        self.assertContains(response, reverse("cleaned-skill-csv-download", args=["jobs"]))
        self.assertContains(response, reverse("cleaned-skill-csv-download", args=["summary"]))
        self.assertContains(response, reverse("course-skill-training-readiness"))
        self.assertContains(response, "Job Skills")
        self.assertContains(response, "Course Skills")
        self.assertContains(response, "Merged Skills")
        self.assertContains(response, "NER skill evidence extracted from job adverts")
        self.assertContains(response, "Combined job and course NER skill evidence")
        self.assertContains(response, "All sectors")
        self.assertContains(response, "All job titles")
        self.assertContains(response, "Business Strategy")
        self.assertContains(response, "Strategy Manager")
        self.assertContains(response, "Run Time Series Forecast")
        self.assertContains(response, "Run All Skills Forecast")
        self.assertContains(response, "forecastYearFrom")
        self.assertContains(response, "forecastYearTo")
        self.assertContains(response, "forecastApiUrl")
        self.assertContains(response, "Semantic Skill Clusters")
        self.assertContains(response, "Full vector view")
        self.assertContains(response, "id=\"fullVectorViewBtn\"")
        self.assertContains(response, "Loading vector view...")
        self.assertContains(response, "Explain top skill evidence chart")
        self.assertContains(response, "Explain skill heatmap")
        self.assertContains(response, "Explain skill evidence network")
        self.assertContains(response, "id=\"skillNetworkLegend\"")
        self.assertContains(response, "Skill nodes")
        self.assertContains(response, "mode:kind === 'skill' ? 'markers' : 'markers+text'")
        self.assertContains(response, "Evidence rows")
        self.assertContains(response, "Explain skill demand forecast")
        self.assertContains(response, "Explain semantic skill clusters")
        self.assertContains(response, "skillForecastPlot")
        self.assertContains(response, "skillClusterPlot")
        self.assertContains(response, "clusterResetBtn")
        self.assertContains(response, "Click a skill to focus its neighbourhood")
        self.assertContains(response, "Connected skills are highlighted")
        self.assertContains(response, "extracted_year")
        self.assertContains(response, "Evidence ID")
        self.assertContains(response, "Strategy module")
        self.assertContains(csv_response, "chunk_id,entity_id,skill")
        self.assertContains(csv_response, "sector,job_title,extracted_date,extracted_year")
        self.assertContains(csv_response, "skill-strategy")
        self.assertContains(course_dataset_response, "dataset_row_type,course_id,course_code")
        self.assertContains(course_dataset_response, "token,")
        self.assertContains(course_dataset_response, "B-SKILL")
        self.assertContains(course_dataset_response, "I-SKILL")
        self.assertContains(course_dataset_response, "skill-data-analysis")
        visual_csv_response = self.client.get(reverse("data-export-visual-export"))
        self.assertContains(visual_csv_response, "visual,dimension_1,dimension_2,dimension_3,value,notes")
        self.assertContains(visual_csv_response, "top_skill_evidence")
        self.assertContains(visual_csv_response, "skill_heatmap")

        job_response = self.client.get(reverse("data-export"), {"source": "job"})
        course_response = self.client.get(reverse("data-export"), {"source": "module"})
        sector_response = self.client.get(reverse("data-export"), {"sector": "Business Strategy"})
        title_response = self.client.get(reverse("data-export"), {"job_title": "Strategy Manager"})

        self.assertContains(job_response, "source=job")
        self.assertContains(job_response, "chunk-job-strategy")
        self.assertNotContains(job_response, "Strategy module")
        self.assertContains(course_response, "source=module")
        self.assertContains(course_response, "Strategy module")
        self.assertNotContains(course_response, "chunk-job-strategy")
        self.assertContains(sector_response, "chunk-job-strategy")
        self.assertNotContains(sector_response, "Strategy module")
        self.assertContains(title_response, "chunk-job-strategy")
        self.assertNotContains(title_response, "Strategy module")

    def test_data_export_forecast_api_uses_full_filtered_year_distribution(self):
        self.job.category = "Business Strategy"
        self.job.date_posted = date(2024, 5, 20)
        self.job.skill_entities = [{
            "id": "skill-strategy",
            "chunk_id": "chunk-job-strategy",
            "skill": "strategy",
            "label": "SKILL",
            "tier": "explicit",
            "skill_type": "business",
            "source": "ner",
            "confidence": 0.96,
            "mention_count": 1,
        }]
        self.job.save(update_fields=["category", "date_posted", "skill_entities"])
        JobAdvert.objects.create(
            title="Strategy Lead",
            category="Business Strategy",
            description="Strategy and planning",
            date_posted=date(2025, 6, 11),
            skill_entities=[{
                "id": "skill-strategy-2025",
                "chunk_id": "chunk-job-strategy-2025",
                "skill": "strategy",
                "label": "SKILL",
                "tier": "explicit",
                "skill_type": "business",
                "source": "ner",
                "confidence": 0.94,
                "mention_count": 1,
            }],
        )

        response = self.client.get(reverse("data-export-forecast-api"), {"source": "job"})
        data = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["has_data"])
        self.assertEqual(data["years"], [2024, 2025])
        strategy = next(item for item in data["series"] if item["skill"] == "strategy")
        self.assertEqual(strategy["counts"], [1, 1])

    @override_settings(COURSE_SKILL_NER_MIN_EXAMPLES=1)
    def test_course_skill_training_readiness_api_reports_ready_dataset(self):
        Module.objects.create(
            course=self.course,
            name="Analytics module",
            content="Data analysis supports decisions.",
            skill_entities=[{
                "id": "skill-data-analysis",
                "chunk_id": "chunk-module-data-analysis",
                "skill": "data analysis",
                "label": "SKILL",
                "start": 0,
                "end": 13,
            }],
            skills_extracted=["data analysis"],
        )

        response = self.client.get(reverse("course-skill-training-readiness"))
        data = response.json()

        self.assertTrue(data["ready"])
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["examples"], 1)
        self.assertEqual(data["minimum"], 1)

    def test_cleaned_skill_csv_download_serves_generated_csv(self):
        csv_dir = Path(settings.BASE_DIR) / "csv"
        csv_dir.mkdir(exist_ok=True)
        csv_path = csv_dir / "refined-skill-summary.csv"
        csv_path.write_text("section,label,count\nskill,data analysis,2\n", encoding="utf-8")

        response = self.client.get(reverse("cleaned-skill-csv-download", args=["summary"]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("refined-skill-summary.csv", response["Content-Disposition"])

    def test_skill_vector_space_page_and_api_use_extracted_skill_nodes(self):
        self.job.category = "Business Strategy"
        self.job.skill_entities = [
            {
                "id": "skill-data-analysis",
                "skill": "data analysis",
                "label": "SKILL",
                "skill_type": "technical",
                "source": "ner",
                "confidence": 0.95,
                "mention_count": 2,
            },
            {
                "id": "skill-data-analytics",
                "skill": "data analytics",
                "label": "SKILL",
                "skill_type": "technical",
                "source": "ner",
                "confidence": 0.91,
                "mention_count": 1,
            },
        ]
        self.job.save(update_fields=["category", "skill_entities"])
        Module.objects.create(
            course=self.course,
            name="Analytics module",
            content="Data analysis for managers",
            skill_entities=[{
                "id": "module-skill-data-analysis",
                "skill": "data analysis",
                "label": "SKILL",
                "skill_type": "technical",
                "source": "ner",
                "confidence": 0.88,
                "mention_count": 1,
            }],
        )

        page_response = self.client.get(reverse("skill-vector-space"))
        api_response = self.client.get(reverse("skill-vector-space-api"))
        data = api_response.json()

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, "Skill Vector Space")
        self.assertContains(page_response, "3d-force-graph")
        self.assertContains(page_response, "vectorApiUrl")
        self.assertContains(page_response, "cosine-similarity strands")
        self.assertContains(page_response, "vectorSkillFilter")
        self.assertContains(page_response, "vectorSectorFilter")
        self.assertContains(page_response, "vectorJobFilter")
        self.assertContains(page_response, "vectorCourseFilter")
        self.assertContains(page_response, "vectorExtractorFilter")
        self.assertContains(page_response, "Advanced Mode")
        self.assertContains(page_response, "Advanced Comparative View")
        self.assertContains(page_response, "vectorAdvancedGroups")
        self.assertContains(page_response, "Qualifications")
        self.assertContains(page_response, "Tick multiple jobs, qualifications, sectors, skills, or extractors")
        self.assertContains(page_response, "clearVectorFiltersBtn")
        self.assertContains(page_response, "Download Visual CSV")
        self.assertContains(page_response, "Explain semantic vector space")
        self.assertContains(page_response, "Root job and course nodes connect to extracted skills")
        self.assertTrue(data["has_visual_data"])
        self.assertIn("job-root", {node["group"] for node in data["nodes"]})
        self.assertIn("course-root", {node["group"] for node in data["nodes"]})
        self.assertIn("skill", {node["group"] for node in data["nodes"]})
        self.assertIn("data analysis", {node["label"] for node in data["nodes"]})
        self.assertTrue(any(node.get("description") for node in data["nodes"] if node["group"] != "skill"))
        self.assertIn("evidence", {edge["group"] for edge in data["edges"]})
        self.assertIn("similarity", {edge["group"] for edge in data["edges"]})
        self.assertTrue(any("Cosine similarity" in edge["title"] for edge in data["edges"]))
        csv_response = self.client.get(reverse("skill-vector-space-export"))
        self.assertContains(csv_response, "record_type,id,source,target,label,group,value")
        self.assertContains(csv_response, "node")
        self.assertContains(csv_response, "edge")

    def test_skill_vector_space_api_accepts_multi_value_filters(self):
        self.job.title = "CEO"
        self.job.category = "Leadership"
        self.job.skill_entities = [{
            "id": "skill-leadership",
            "skill": "leadership",
            "label": "SKILL",
            "skill_type": "soft",
            "source": "ner",
        }]
        self.job.save(update_fields=["title", "category", "skill_entities"])
        second_job = JobAdvert.objects.create(
            title="Data Analyst",
            category="Analytics",
            description="Analytics and SQL",
            skill_entities=[{
                "id": "skill-data-analysis",
                "skill": "data analysis",
                "label": "SKILL",
                "skill_type": "technical",
                "source": "ner",
            }],
        )

        response = self.client.get(
            reverse("skill-vector-space-api"),
            [("source", "job"), ("job_title", "CEO"), ("job_title", "Data Analyst")],
        )
        data = response.json()

        self.assertTrue(data["has_visual_data"])
        labels = {node["full_label"] for node in data["nodes"] if node["group"] == "job-root"}
        self.assertTrue(any("CEO" in label for label in labels))
        self.assertTrue(any("Data Analyst" in label for label in labels))
        self.assertFalse(any(node["group"] == "course-root" for node in data["nodes"]))

    def test_summary_page_counts_unique_courses_and_jobs(self):
        Course.objects.create(code="MBA102", name="MBA", university_name="University of Johannesburg")
        Module.objects.create(course=self.course, name="Strategy", content="Strategy")
        Module.objects.create(course=self.course, name="Leadership", content="Leadership")
        JobAdvert.objects.create(title="Strategy Manager", company="Alpha", category="Management", description="Leadership")
        JobAdvert.objects.create(title="Data Analyst", company="Beta", category="Analytics", description="SQL and analytics")

        response = self.client.get(reverse("summary"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unique Courses and Jobs")
        self.assertContains(response, "Top Course Names")
        self.assertContains(response, "Top Job Titles")
        self.assertContains(response, "Top Universities")
        self.assertContains(response, "Top Job Categories")
        self.assertContains(response, "Summary")

    def test_data_export_infers_sector_when_source_has_no_category(self):
        self.job.title = "Cyber Security Analyst"
        self.job.description = "Cloud security, compliance, risk governance, and SQL reporting."
        self.job.category = ""
        self.job.skill_entities = [{
            "id": "skill-security-governance",
            "skill": "security governance",
            "label": "SKILL",
            "skill_type": "technical",
            "source": "ner",
        }]
        self.job.save(update_fields=["title", "description", "category", "skill_entities"])
        Module.objects.create(
            course=self.course,
            name="Financial accounting analytics",
            content="Accounting, tax, budget control, and finance reporting.",
            skills_extracted=["accounting"],
        )

        response = self.client.get(reverse("skill-vector-space-api"))
        data = response.json()
        sectors = {node["sector"] for node in data["nodes"] if node.get("sector")}

        self.assertIn("Cybersecurity and Risk", sectors)
        self.assertIn("Finance and Accounting", sectors)
        self.assertNotIn("Unclassified", sectors)

    def test_skill_entity_update_converts_legacy_skill_to_reviewed_entity(self):
        self.job.skills_extracted = ["leadership"]
        self.job.save(update_fields=["skills_extracted"])

        response = self.client.post(reverse("skill-entity-update"), {
            "source_type": "job",
            "source_id": self.job.id,
            "entity_id": "skill-leadership",
            "chunk_id": f"job-{self.job.id}-skill-leadership",
            "skill": "executive leadership",
            "label": "SKILL",
            "tier": "reviewed",
            "skill_type": "soft",
        })

        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.skills_extracted, ["executive leadership"])
        self.assertEqual(self.job.skill_entities[0]["label_status"], "reviewed")

    def test_candidate_skill_is_visible_but_not_counted_until_reviewed(self):
        self.job.skill_entities = [{
            "id": "skill-accounting",
            "chunk_id": f"job-{self.job.id}-skill-accounting",
            "skill": "accounting",
            "label": "SKILL",
            "tier": "explicit",
            "skill_type": "business",
            "source": "legacy",
            "label_status": "legacy",
        }, {
            "id": "skill-sap-financial-accounting",
            "chunk_id": f"job-{self.job.id}-candidate-skill-sap-financial-accounting",
            "skill": "sap financial accounting",
            "label": "SKILL",
            "tier": "candidate",
            "skill_type": "business",
            "source": "ollama_verification",
            "label_status": "candidate",
        }]
        self.job.skills_extracted = ["accounting"]
        self.job.save(update_fields=["skill_entities", "skills_extracted"])

        response = self.client.get(reverse("data-export"), {"label_status": "candidate"})

        self.assertContains(response, "sap financial accounting")
        self.assertEqual(current_extracted_skill_rows(JobAdvert), [{"skill": "accounting", "frequency": 1}])

        self.client.post(reverse("skill-entity-update"), {
            "source_type": "job",
            "source_id": self.job.id,
            "entity_id": "skill-sap-financial-accounting",
            "chunk_id": f"job-{self.job.id}-candidate-skill-sap-financial-accounting",
            "skill": "sap financial accounting",
            "label": "SKILL",
            "tier": "reviewed",
            "skill_type": "business",
        })

        self.job.refresh_from_db()
        self.assertEqual(self.job.skills_extracted, ["accounting", "sap financial accounting"])
        reviewed = [entity for entity in self.job.skill_entities if entity["skill"] == "sap financial accounting"][0]
        self.assertEqual(reviewed["label_status"], "reviewed")

    def test_human_oversight_page_surfaces_ai_candidates_and_checks(self):
        self.job.title = "SAP Finance Analyst"
        self.job.description = "SAP financial accounting, treasury solutions, reporting, workshops, governance, and controls."
        self.job.skill_entities = [{
            "id": "skill-sap-financial-accounting",
            "chunk_id": f"job-{self.job.id}-candidate-skill-sap-financial-accounting",
            "skill": "sap financial accounting",
            "label": "SKILL",
            "tier": "candidate",
            "skill_type": "business",
            "source": "ollama_verification",
            "label_status": "candidate",
        }]
        self.job.save(update_fields=["title", "description", "skill_entities"])

        response = self.client.get(reverse("human-oversight"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Human Oversight")
        self.assertContains(response, "sap financial accounting")
        self.assertContains(response, "AI Suggestions Waiting For Human Review")
        self.assertContains(response, "Run Verification")
        self.assertContains(response, "Aliases")
        self.assertContains(response, "Rerun Analysis With Approved Aliases")
        self.assertContains(response, "Start Hourly Reviewed Alias Refresh")

    @override_settings(BERT_SKILL_NER_ENABLED=False, DYNAMIC_SKILL_ALIAS_ENABLED=True)
    def test_approved_skill_alias_is_loaded_by_extractor(self):
        from analysis.spacyskillextraction import SpacySkillExtractor

        SkillAlias.objects.create(
            canonical_skill="power bi",
            alias="powerbi",
            status="approved",
            source="human_review",
            confidence=1.0,
        )

        extractor = SpacySkillExtractor()
        skills = extractor.extract("Build PowerBI dashboards for finance teams.")

        self.assertIn("power bi", skills)

    def test_alias_review_approves_candidate_for_next_run(self):
        alias = SkillAlias.objects.create(
            canonical_skill="business intelligence",
            alias="bi reporting",
            status="candidate",
            source="evidence",
            evidence_count=3,
        )

        response = self.client.post(reverse("skill-alias-review"), {
            "alias_id": alias.id,
            "canonical_skill": "business intelligence",
            "alias": "BI reporting",
            "action": "approve",
            "next": reverse("human-oversight"),
        })

        self.assertEqual(response.status_code, 302)
        alias.refresh_from_db()
        self.assertEqual(alias.status, "approved")
        self.assertEqual(alias.alias, "bi reporting")
        self.assertEqual(alias.source, "human_review")

    def test_hourly_alias_refresh_queues_background_loop(self):
        with patch("dashboard.views.start_reviewed_alias_refresh_task") as start_task:
            response = self.client.post(reverse("skill-alias-hourly-refresh"), {
                "interval_seconds": "3600",
                "max_jobs": "12",
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("task-list"))
        task = TaskRecord.objects.get(run_name="Reviewed Alias Hourly Refresh: every 60 min")
        start_task.assert_called_once_with(interval_seconds=3600, record_id=task.id, max_jobs=12)

    def test_human_oversight_run_checks_queues_background_verification(self):
        with patch("dashboard.views.start_skill_verification_task") as start_task:
            response = self.client.post(reverse("human-oversight"), {
                "max_jobs": "4",
                "max_modules": "5",
                "model": "ministral-3:3b",
                "use_llm": "1",
                "save_candidates": "1",
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("task-list"))
        task = TaskRecord.objects.get(run_name="Skill Verification: 4 jobs / 5 courses")
        start_task.assert_called_once()
        kwargs = start_task.call_args.kwargs
        self.assertEqual(kwargs["record_id"], task.id)
        self.assertEqual(kwargs["max_jobs"], 4)
        self.assertEqual(kwargs["max_modules"], 5)
        self.assertTrue(kwargs["use_llm"])
        self.assertTrue(kwargs["save_candidates"])

    def test_skill_entity_update_can_edit_evidence_text_and_metadata(self):
        self.job.skill_entities = [{
            "id": "skill-accounting",
            "chunk_id": f"job-{self.job.id}-skill-accounting",
            "skill": "accounting",
            "label": "SKILL",
            "tier": "explicit",
            "skill_type": "business",
            "source": "legacy",
            "text": "accounting",
            "confidence": 0.4,
            "mention_count": 1,
            "label_status": "candidate",
        }]
        self.job.save(update_fields=["skill_entities"])

        response = self.client.post(reverse("skill-entity-update"), {
            "source_type": "job",
            "source_id": self.job.id,
            "entity_id": "skill-accounting",
            "chunk_id": f"job-{self.job.id}-skill-accounting",
            "skill": "sap financial accounting",
            "text": "SAP financial accounting in monthly reporting",
            "label": "SKILL",
            "tier": "reviewed",
            "skill_type": "technical",
            "source": "human_review",
            "confidence": "0.92",
            "mention_count": "3",
        })

        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        entity = self.job.skill_entities[0]
        self.assertEqual(entity["skill"], "sap financial accounting")
        self.assertEqual(entity["text"], "SAP financial accounting in monthly reporting")
        self.assertEqual(entity["source"], "human_review")
        self.assertEqual(entity["confidence"], 0.92)
        self.assertEqual(entity["mention_count"], 3)
        self.assertEqual(self.job.skills_extracted, ["sap financial accounting"])

    def test_skill_entity_create_and_delete_are_available_for_human_crud(self):
        create_response = self.client.post(reverse("skill-entity-create"), {
            "source_type": "job",
            "source_id": self.job.id,
            "skill": "treasury solutions",
            "text": "Treasury solutions implementation",
            "label": "SKILL",
            "tier": "reviewed",
            "skill_type": "business",
            "source": "human",
            "confidence": "1",
            "mention_count": "1",
        })

        self.assertEqual(create_response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.skills_extracted, ["treasury solutions"])
        entity = self.job.skill_entities[0]
        self.assertEqual(entity["text"], "Treasury solutions implementation")

        delete_response = self.client.post(reverse("skill-entity-delete"), {
            "source_type": "job",
            "source_id": self.job.id,
            "entity_id": entity["id"],
            "chunk_id": entity["chunk_id"],
        })

        self.assertEqual(delete_response.status_code, 302)
        self.job.refresh_from_db()
        self.assertEqual(self.job.skill_entities, [])
        self.assertEqual(self.job.skills_extracted, [])

    def test_bulk_approve_candidate_buttons_can_target_jobs_or_courses(self):
        self.job.skill_entities = [{
            "id": "skill-sap-financial-accounting",
            "chunk_id": f"job-{self.job.id}-candidate-skill-sap-financial-accounting",
            "skill": "sap financial accounting",
            "label": "SKILL",
            "tier": "candidate",
            "skill_type": "business",
            "source": "ollama_verification",
            "label_status": "candidate",
        }]
        self.job.save(update_fields=["skill_entities"])
        module = Module.objects.create(
            course=self.course,
            name="Strategy module",
            content="Scenario planning",
            skill_entities=[{
                "id": "skill-scenario-planning",
                "chunk_id": "module-candidate-skill-scenario-planning",
                "skill": "scenario planning",
                "label": "SKILL",
                "tier": "candidate",
                "skill_type": "business",
                "source": "ollama_verification",
                "label_status": "candidate",
            }],
        )

        response = self.client.post(reverse("skill-entity-bulk-approve"), {
            "source_type": "job",
            "next": reverse("human-oversight"),
        })

        self.assertEqual(response.status_code, 302)
        self.job.refresh_from_db()
        module.refresh_from_db()
        self.assertEqual(self.job.skill_entities[0]["label_status"], "reviewed")
        self.assertEqual(self.job.skills_extracted, ["sap financial accounting"])
        self.assertEqual(module.skill_entities[0]["label_status"], "candidate")
        self.assertEqual(module.skills_extracted, [])

        self.client.post(reverse("skill-entity-bulk-approve"), {
            "source_type": "module",
            "next": reverse("human-oversight"),
        })

        module.refresh_from_db()
        self.assertEqual(module.skill_entities[0]["label_status"], "reviewed")
        self.assertEqual(module.skills_extracted, ["scenario planning"])

    def test_results_page_renders_heatmap_scatter_and_suggestions(self):
        self.course.university_name = "University of Johannesburg"
        self.course.country = "South Africa"
        self.course.save(update_fields=["university_name", "country"])
        self.job.skills_extracted = ["strategy", "analytics", "finance"]
        self.job.save(update_fields=["skills_extracted"])
        Module.objects.create(
            course=self.course,
            name="Strategy Evidence",
            content="Strategy communication leadership",
            skills_extracted=["strategy", "communication", "leadership"],
        )
        run = AnalysisRun.objects.create(name="Run with visuals", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.32,
            matched_skills=["strategy"],
            missing_skills=["analytics", "finance"],
        )

        response = self.client.get(
            reverse("analysis-results"),
            {"run": run.id, "school": "University of Johannesburg", "threshold": "55"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course Alignment Heatmap")
        self.assertContains(response, "Historic Analysis Runs")
        self.assertContains(response, "Select any saved run to revisit its scores")
        self.assertContains(response, "Download Visual CSV")
        self.assertContains(response, "Download Report Visuals")
        self.assertContains(response, "Skill Correlation Heatmap")
        self.assertContains(response, "Correlation Structure Between Skills")
        self.assertContains(response, "Role Skill Divergence")
        self.assertContains(response, "Diverging bar chart of skill emphasis against the job-market baseline")
        self.assertContains(response, "role-divergence-data")
        self.assertContains(response, "results-comparison-data")
        self.assertContains(response, "Comparison Mode")
        self.assertContains(response, "Compare Role Profiles")
        self.assertContains(response, "resultsComparisonGroups")
        self.assertContains(response, "The skill correlation matrix and role-skill divergence charts update")
        self.assertContains(response, "roleDivergencePlots")
        self.assertContains(response, "roleDivergenceSearch")
        self.assertContains(response, "Choose role")
        self.assertContains(response, "Start typing a role name from the database")
        self.assertContains(response, "roleDivergenceRoleNames")
        self.assertContains(response, "roleDivergenceLimit")
        self.assertContains(response, "Top 25")
        self.assertContains(response, "Unique Job Roles In Current Comparison")
        self.assertContains(response, "These distinct job-role names reflect the current role-divergence selection")
        self.assertContains(response, "Total:")
        self.assertContains(response, "Pearson correlation coefficients across role requirement profiles")
        self.assertContains(response, "skill-correlation-data")
        self.assertContains(response, "data-tab=\"heatmapTab\"")
        self.assertContains(response, "gapPlotlyHeatmap")
        self.assertContains(response, "gap-heatmap-data")
        self.assertContains(response, "plotly-2.35.2.min.js")
        self.assertContains(response, "type:'heatmap'")
        self.assertContains(response, "Explain course alignment heatmap")
        self.assertContains(response, "Explain matched versus missing scatter")
        self.assertContains(response, "Explain skill correlation heatmap")
        self.assertContains(response, "Explain role skill divergence")
        self.assertContains(response, "Each number inside a cell is the count of job adverts")
        self.assertContains(response, "Each cell compares two skills across job role profiles")
        self.assertContains(response, "Red bars show skills that are more prominent for that role")
        self.assertContains(response, "Matched vs Missing Scatter")
        self.assertContains(response, "Curriculum Recommendations")
        self.assertContains(response, "Mismatch risk")
        self.assertContains(response, "Based on the analysed course-to-job data, the curriculum should strengthen analytics and finance")
        self.assertContains(response, "The current evidence already shows coverage in strategy")
        self.assertContains(response, "Skill Suggestions")
        self.assertContains(response, "School Skill Matrix")
        self.assertContains(response, "Filter by course, job, school, or country")
        self.assertContains(response, "data-school=\"university of johannesburg\"")
        self.assertContains(response, "data-country=\"south africa\"")
        self.assertContains(response, "Demand evidence 1")
        self.assertContains(response, "Jobs 1")
        self.assertContains(response, "Courses covered 0")
        self.assertContains(response, "Gap evidence 1")
        self.assertContains(response, "Gap 100%")
        self.assertContains(response, "Coverage 0%")
        self.assertContains(response, "Demand evidence = covered evidence + gap evidence")
        self.assertContains(response, "Gap % = gap evidence / demand evidence")
        self.assertContains(response, "Job Evidence Skills")
        self.assertContains(response, "Course Evidence Skills")
        self.assertContains(response, "communication")
        self.assertContains(response, "Export Report")
        csv_response = self.client.get(reverse("analysis-visual-export"), {"run": run.id, "school": "University of Johannesburg", "threshold": "55"})
        self.assertContains(csv_response, "visual,run,course_id,course,school,job_id,job")
        self.assertContains(csv_response, "skill_correlation_heatmap")
        self.assertContains(csv_response, "course_to_job_gap_heatmap")
        self.assertContains(csv_response, "matched_vs_missing_scatter")

    def test_visual_data_builds_skill_correlation_matrix_from_role_profiles(self):
        analyst = JobAdvert.objects.create(title="Analyst", description="Analytics finance")
        architect = JobAdvert.objects.create(title="Architect", description="Architecture leadership")
        run = AnalysisRun.objects.create(name="Correlation run", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.6,
            matched_skills=["strategy"],
            missing_skills=["analytics"],
        )
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=analyst,
            similarity_score=0.6,
            matched_skills=[],
            missing_skills=["analytics", "finance"],
        )
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=architect,
            similarity_score=0.6,
            matched_skills=["strategy"],
            missing_skills=["leadership"],
        )

        visual_data = build_results_visual_data(list(GapResult.objects.filter(run=run).select_related("course", "job")), 55)
        matrix = visual_data["skill_correlation_matrix"]
        divergence = visual_data["role_skill_divergence"]
        analytics_index = matrix["skills"].index("analytics")
        finance_index = matrix["skills"].index("finance")

        self.assertEqual(matrix["profile_count"], 3)
        self.assertEqual(matrix["values"][analytics_index][analytics_index], 1.0)
        self.assertGreater(matrix["values"][analytics_index][finance_index], 0)
        self.assertEqual(divergence["profile_count"], 3)
        self.assertTrue(any(row["role"] == "Analyst" for row in divergence["roles"]))
        analyst_row = next(row for row in divergence["roles"] if row["role"] == "Analyst")
        self.assertIn("finance", analyst_row["skills"])
        self.assertGreater(analyst_row["values"][analyst_row["skills"].index("finance")], 0)

    def test_model_validation_page_prints_notebook_outputs_and_equations(self):
        self.course.university_name = "University of Johannesburg"
        self.course.save(update_fields=["university_name"])
        self.job.skills_extracted = ["strategy", "analytics"]
        self.job.save(update_fields=["skills_extracted"])
        Module.objects.create(
            course=self.course,
            name="Strategy Evidence",
            content="Strategy and communication",
            skills_extracted=["strategy", "communication"],
        )
        run = AnalysisRun.objects.create(name="Validation run", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.72,
            score_breakdown={
                "model": "semantic_skill_confidence_decision_tree_ensemble",
                "semantic_score": 0.7,
                "skill_score": 0.5,
                "confidence_score": 0.8,
                "decision_tree_score": 0.82,
                "final_score": 0.72,
            },
            matched_skills=["strategy"],
            missing_skills=["analytics"],
        )

        response = self.client.get(reverse("model-validation"), {"run": run.id})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Model Validation Notebook")
        self.assertContains(response, "Publication-facing audit view")
        self.assertContains(response, "Final Match Score Equation")
        self.assertContains(response, "\\operatorname{finalScore}")
        self.assertContains(response, "Pipeline Extraction Outputs")
        self.assertContains(response, "Skill Correlation Cell Validation")
        self.assertContains(response, "Role Skill Divergence Cell Validation")
        self.assertContains(response, "Skill Gap Matrix Validation")
        self.assertContains(response, "Show code used for this cell")
        self.assertContains(response, "build_skill_correlation_matrix(results)")
        self.assertContains(response, "build_role_skill_divergence(results)")
        self.assertContains(response, "Output")
        self.assertContains(response, "mathjax@3/es5/tex-svg.js")
        self.assertContains(response, "Validation run")
        self.assertContains(response, "strategy")
        self.assertContains(response, "analytics")

    def test_dashboard_visual_csv_export_returns_source_rows(self):
        run = AnalysisRun.objects.create(name="Run with dashboard visuals", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.72,
            matched_skills=["strategy"],
            missing_skills=["analytics"],
        )
        SkillMatrix.objects.create(run=run, source="jobs", skill="strategy", frequency=3)

        response = self.client.get(reverse("dashboard-visual-export"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "visual,run,source,course_id,course,job_id,job")
        self.assertContains(response, "score_bucket_summary")
        self.assertContains(response, "skill_demand_vs_curriculum")
        self.assertContains(response, "course_to_job_network_edge")

    def test_technical_report_export_queues_background_task(self):
        run = AnalysisRun.objects.create(name="Report run", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.66,
            matched_skills=["strategy"],
            missing_skills=["analytics"],
        )

        with patch("dashboard.views.start_research_paper_task") as start_task:
            response = self.client.get(reverse("technical-report-export"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("task-list"))
        task = TaskRecord.objects.get(run_name="Research Paper Export: Report run")
        start_task.assert_called_once_with(record_id=task.id, run_id=run.id)

    def test_technical_report_download_returns_generated_word_document(self):
        from docx import Document
        from io import BytesIO
        from analysis.tasks import _do_research_paper_report

        self.course.university_name = "University of Johannesburg"
        self.course.country = "South Africa"
        self.course.save(update_fields=["university_name", "country"])
        run = AnalysisRun.objects.create(name="Report run", status="done")
        GapResult.objects.create(
            run=run,
            course=self.course,
            job=self.job,
            similarity_score=0.66,
            matched_skills=["strategy"],
            missing_skills=["analytics"],
        )
        SkillMatrix.objects.create(run=run, source="jobs", skill="analytics", frequency=4)
        SkillMatrix.objects.create(run=run, source="courses", skill="strategy", frequency=2)

        task = TaskRecord.objects.create(run_name="Research Paper Export: Report run")
        _do_research_paper_report(task.id, run.id)
        task.refresh_from_db()
        response = self.client.get(reverse("technical-report-download", args=[task.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertIn("curriculummatch-research-paper.docx", response["Content-Disposition"])
        content = b"".join(response.streaming_content)
        self.assertGreater(len(content), 1000)
        document = Document(BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("CurriculumMatch Research Paper Export", text)
        self.assertIn("Dashboard Chart. Skill Demand vs Curriculum", text)
        self.assertIn("Reference to Dashboard Chart. Skill Demand vs Curriculum", text)
        self.assertNotIn("Dashboard Chart. Top Course-to-Job Similarities", text)
        self.assertNotIn("Dashboard Plotly Chart. Course-to-Job Similarity Cross-tab", text)
        self.assertNotIn("Figure 4. School Summary", text)
        self.assertIn("Results Chart. Course Alignment Score-Band Heatmap", text)
        self.assertIn("3. Visual Evidence", text)
        self.assertIn("Figure 1. Score Distribution", text)
        self.assertIn("CRISP-DM data-mining process", text)
        self.assertIn("Methodology Diagram 0. CRISP-DM Data-Mining Process", text)
        self.assertIn("Methodology Diagram 1. End-to-End Iterative Pipeline", text)
        self.assertIn("Methodology Diagram 3. Validation Calculations", text)
        self.assertIn("Methodology Diagram 4. Transformer Architecture for Skill Tagging", text)
        self.assertIn("Methodology Diagram 5. Runtime Flow of Events", text)
        self.assertIn("Human cleaning is therefore part of the data-mining loop", text)
        self.assertIn("Supporting Equations", text)
        self.assertIn("Final weighted score", text)
        self.assertIn("Figure 5. Skill Gap Matrix", text)
        self.assertIn("Data Export Plotly Chart. Top Skill Evidence", text)
        self.assertIn("Data Export Plotly Chart. Semantic Association Clusters", text)
        self.assertIn("5. Human Oversight and Learning", text)
        self.assertIn("6. Limitations", text)
        artifact_roots = list((Path(settings.BASE_DIR) / "paper_artifacts").glob("*report-run*"))
        self.assertTrue(artifact_roots)
        image_files = list((artifact_roots[0] / "images").glob("*.png"))
        self.assertTrue(image_files)
        self.assertTrue((artifact_roots[0] / "images" / "methodology-crisp-dm-data-mining-process.png").exists())
        self.assertTrue((artifact_roots[0] / "images" / "methodology-transformer-architecture.png").exists())
        self.assertTrue((artifact_roots[0] / "images" / "methodology-runtime-flow-of-events.png").exists())
        self.assertTrue((artifact_roots[0] / "visual_manifest.md").exists())

        image_response = self.client.get(reverse("technical-report-visual-download", args=[run.id, "dashboard-skill-demand-vs-curriculum"]))
        self.assertEqual(image_response.status_code, 200)
        image_content = b"".join(image_response.streaming_content)
        self.assertTrue(image_content.startswith(b"\x89PNG"))

        archive_response = self.client.get(reverse("technical-report-visual-archive", args=[run.id]))
        self.assertEqual(archive_response.status_code, 200)
        self.assertEqual(archive_response["Content-Type"], "application/zip")

    def test_business_recommendations_refine_technical_skill_noise(self):
        insight = recommendation_skill_insight(
            {
                "data analysis": 4,
                "c++": 3,
                "c#": 3,
                "sql": 2,
                "leadership": 1,
            },
            {"artificial intelligence": 3, "communication": 2},
        )
        rows = refine_skill_rows_for_business([
            {"skill": "c++", "frequency": 5},
            {"skill": "data analysis", "frequency": 4},
            {"skill": "sql", "frequency": 2},
        ])

        self.assertIn("Based on the analysed course-to-job data", insight)
        self.assertIn("business analytics", insight)
        self.assertIn("data-driven decision making", insight)
        self.assertNotIn("c++", insight)
        self.assertNotIn("c#", insight)
        self.assertEqual([row["skill"] for row in rows], ["business analytics", "data-driven decision making"])
