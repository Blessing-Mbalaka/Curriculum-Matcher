from django.db import models


class Course(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    university_name = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def skills_extracted(self):
        skills = {
            skill
            for module in self.modules.all()
            for skill in (module.skills_extracted or [])
        }
        return sorted(skills)


class Module(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    name = models.CharField(max_length=255)
    university_name = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=120, blank=True)
    content = models.TextField(help_text="Paste the full syllabus / content for this module")
    skills_extracted = models.JSONField(default=list, blank=True)
    skill_entities = models.JSONField(default=list, blank=True)
    vector = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return f"{self.course.code} › {self.name}"
