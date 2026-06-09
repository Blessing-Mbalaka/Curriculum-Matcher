from datetime import date
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from analysis.models import AnalysisRun, GapResult, SkillMatrix
from analysis.models import TaskRecord
from courses.models import Course
from courses.models import Module
from dashboard.views import current_extracted_skill_rows, recommendation_skill_insight, refine_skill_rows_for_business
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

    def test_results_page_renders_heatmap_scatter_and_suggestions(self):
        self.course.university_name = "University of Johannesburg"
        self.course.country = "South Africa"
        self.course.save(update_fields=["university_name", "country"])
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
        self.assertContains(response, "Course-to-Job Gap Heatmap")
        self.assertContains(response, "Ranked jobs")
        self.assertContains(response, "Matrix heatmap")
        self.assertContains(response, "Show matrix job labels")
        self.assertContains(response, "gapVisualMode")
        self.assertContains(response, "gapCourseFilter")
        self.assertContains(response, "gapJobLegend")
        self.assertContains(response, "data-tab=\"heatmapTab\"")
        self.assertContains(response, "gapPlotlyHeatmap")
        self.assertContains(response, "gap-heatmap-data")
        self.assertContains(response, "plotly-2.35.2.min.js")
        self.assertContains(response, "type:'heatmap'")
        self.assertContains(response, "Explain course alignment heatmap")
        self.assertContains(response, "Explain matched versus missing scatter")
        self.assertContains(response, "Explain course-to-job gap heatmap")
        self.assertContains(response, "Each number inside a cell is the count of job adverts")
        self.assertContains(response, "The number shown in hover is the match percentage, not a count")
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
        self.assertContains(response, "Export Report")
        csv_response = self.client.get(reverse("analysis-visual-export"), {"run": run.id, "school": "University of Johannesburg", "threshold": "55"})
        self.assertContains(csv_response, "visual,run,course_id,course,school,job_id,job")
        self.assertContains(csv_response, "course_to_job_gap_heatmap")
        self.assertContains(csv_response, "matched_vs_missing_scatter")

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

    def test_technical_report_export_returns_word_document(self):
        from docx import Document
        from io import BytesIO

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

        response = self.client.get(reverse("technical-report-export"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertIn("curriculummatch-technical-report.docx", response["Content-Disposition"])
        self.assertGreater(len(response.content), 1000)
        document = Document(BytesIO(response.content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("Methodology Visual Pipeline", text)
        self.assertIn("Figure 1. End-to-end methodology pipeline.", text)
        self.assertIn("Figure 2. Evidence funnel.", text)
        self.assertIn("Figure 3. High-level graph legend.", text)

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
