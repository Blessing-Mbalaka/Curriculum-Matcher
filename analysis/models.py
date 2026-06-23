from django.db import models
from courses.models import Course
from jobs.models import JobAdvert



class AnalysisRun(models.Model):
    STATUS = [("pending","Pending"),("running","Running"),("done","Done"),("error","Error")]
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS, default="pending")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} [{self.status}]"


class GapResult(models.Model):
    run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="results")
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    job = models.ForeignKey(JobAdvert, on_delete=models.CASCADE)
    similarity_score = models.FloatField(default=0)
    score_breakdown = models.JSONField(default=dict)
    matched_skills = models.JSONField(default=list)
    missing_skills = models.JSONField(default=list)
    extra_skills = models.JSONField(default=list)

    class Meta:
        unique_together = ("run", "course", "job")
        ordering = ["-similarity_score"]

    @property
    def similarity_percent(self):
        return round(self.similarity_score * 100, 1)


class SkillMatrix(models.Model):
    run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="skill_matrices")
    source = models.CharField(max_length=10, choices=[("jobs","Jobs"),("courses","Courses")])
    skill = models.CharField(max_length=100)
    frequency = models.IntegerField(default=0)

    class Meta:
        unique_together = ("run", "source", "skill")
        ordering = ["-frequency"]


class SkillAlias(models.Model):
    STATUS = [
        ("candidate", "Candidate"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]
    SOURCE = [
        ("manual", "Manual"),
        ("evidence", "Evidence"),
        ("human_review", "Human review"),
        ("import", "Import"),
        ("model_suggestion", "Model suggestion"),
        ("seed", "Seed"),
    ]

    canonical_skill = models.CharField(max_length=160)
    alias = models.CharField(max_length=160)
    status = models.CharField(max_length=20, choices=STATUS, default="candidate")
    source = models.CharField(max_length=30, choices=SOURCE, default="evidence")
    confidence = models.FloatField(default=0.0)
    evidence_count = models.PositiveIntegerField(default=1)
    created_from_text = models.TextField(blank=True)
    created_from_doc_id = models.CharField(max_length=120, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("canonical_skill", "alias")
        ordering = ["status", "-evidence_count", "canonical_skill", "alias"]

    def __str__(self):
        return f"{self.alias} -> {self.canonical_skill} [{self.status}]"


class TaskRecord(models.Model):
    STATUS = [("PENDING","Pending"),("STARTED","Started"),("SUCCESS","Success"),("FAILURE","Failure"),("STOPPED","Paused")]
    task_id = models.CharField(max_length=255, unique=False, blank=True)
    run_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS, default="PENDING")
    progress = models.PositiveSmallIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.run_name} [{self.status}]"
