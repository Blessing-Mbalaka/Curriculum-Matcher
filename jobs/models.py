from django.db import models


class JobAdvert(models.Model):
    SOURCE_CHOICES = [
        ("upload", "Manual"),
        ("adzuna", "Adzuna API"),
        ("csv", "CSV Import"),
    ]

    title = models.CharField(max_length=255)
    company = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    description = models.TextField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="upload")
    external_id = models.CharField(max_length=255, blank=True, null=True)
    url = models.URLField(blank=True)
    salary_min = models.IntegerField(null=True, blank=True)
    salary_max = models.IntegerField(null=True, blank=True)
    skills_extracted = models.JSONField(default=list, blank=True)
    vector = models.JSONField(default=list, blank=True)
    date_posted = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        # Prevent true duplicates from API
        unique_together = []

    def __str__(self):
        return f"{self.title} @ {self.company or 'Unknown'}"
