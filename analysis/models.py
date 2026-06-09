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
