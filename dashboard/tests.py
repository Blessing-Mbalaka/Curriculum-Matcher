from django.test import TestCase
from django.urls import reverse

from analysis.models import AnalysisRun, GapResult, SkillMatrix
from analysis.models import TaskRecord
from courses.models import Course
from dashboard.views import recommendation_skill_insight, refine_skill_rows_for_business
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
        self.assertContains(response, "match-tile")
        self.assertContains(response, "Course to job advert")
        self.assertContains(response, "Filter network by school")
        self.assertContains(response, "University of Johannesburg")
        self.assertContains(response, "Filter network by job advert")
        self.assertContains(response, "Strategy Manager")
        self.assertContains(response, "id=\"network3dBtn\"")
        self.assertContains(response, "id=\"networkCrosstabBtn\"")
        self.assertContains(response, "Cross-tab")
        self.assertContains(response, "3d-force-graph")
        self.assertContains(response, "id=\"similarityNetwork3d\"")
        self.assertContains(response, "id=\"similarityCrosstab\"")
        self.assertContains(response, "network-canvas-wrap is-3d")
        self.assertContains(response, "Cosine similarity:")

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
