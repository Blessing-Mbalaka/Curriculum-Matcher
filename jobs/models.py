import hashlib
import re

from django.db import models


def normalize_job_text(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").lower())).strip()


def job_fingerprint(title, company="", location="", description="", recruiter="", job_reference=""):
    normalized = "|".join([
        normalize_job_text(title),
        normalize_job_text(company),
        normalize_job_text(recruiter),
        normalize_job_text(location),
        normalize_job_text(job_reference),
        normalize_job_text(description)[:1200],
    ])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class JobAdvert(models.Model):
    SOURCE_CHOICES = [
        ("upload", "Manual"),
        ("adzuna", "Adzuna API"),
        ("csv", "CSV Import"),
    ]

    title = models.CharField(max_length=255)
    company = models.CharField(max_length=255, blank=True)
    recruiter = models.CharField(max_length=255, blank=True)
    job_reference = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=255, blank=True)
    contract_type = models.CharField(max_length=80, blank=True)
    contract_time = models.CharField(max_length=80, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    summary = models.TextField(blank=True)
    position_info = models.TextField(blank=True)
    description = models.TextField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="upload")
    external_id = models.CharField(max_length=255, blank=True, null=True)
    fingerprint = models.CharField(max_length=64, blank=True, db_index=True, unique=True)
    url = models.URLField(blank=True)
    salary_min = models.IntegerField(null=True, blank=True)
    salary_max = models.IntegerField(null=True, blank=True)
    skills_extracted = models.JSONField(default=list, blank=True)
    skill_entities = models.JSONField(default=list, blank=True)
    vector = models.JSONField(default=list, blank=True)
    source_payload = models.JSONField(default=dict, blank=True)
    date_posted = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id"],
                name="unique_job_source_external_id",
                condition=models.Q(external_id__isnull=False),
            )
        ]

    def __str__(self):
        return f"{self.title} @ {self.company or 'Unknown'}"

    def analysis_text(self):
        parts = [
            self.title,
            self.company,
            self.recruiter,
            self.category,
            self.summary,
            self.position_info,
            self.description,
        ]
        return "\n\n".join(part for part in parts if part)

    def save(self, *args, **kwargs):
        if not self.fingerprint:
            self.fingerprint = job_fingerprint(
                self.title,
                self.company,
                self.location,
                self.analysis_text(),
                self.recruiter,
                self.job_reference,
            )
        super().save(*args, **kwargs)
