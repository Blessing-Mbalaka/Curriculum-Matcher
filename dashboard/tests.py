from django.test import TestCase
from django.urls import reverse

from analysis.models import AnalysisRun, GapResult, SkillMatrix
from analysis.models import TaskRecord
from courses.models import Course
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
