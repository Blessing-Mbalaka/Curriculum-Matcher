from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("courses", "0001_initial"),
        ("jobs", "0001_initial"),
    ]
    operations = [
        migrations.CreateModel(
            name="AnalysisRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("name", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("pending","Pending"),("running","Running"),("done","Done"),("error","Error")], default="pending", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="GapResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="results", to="analysis.analysisrun")),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="courses.course")),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="jobs.jobadvert")),
                ("similarity_score", models.FloatField(default=0)),
                ("matched_skills", models.JSONField(default=list)),
                ("missing_skills", models.JSONField(default=list)),
                ("extra_skills", models.JSONField(default=list)),
            ],
            options={"ordering": ["-similarity_score"], "unique_together": {("run","course","job")}},
        ),
        migrations.CreateModel(
            name="SkillMatrix",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="skill_matrices", to="analysis.analysisrun")),
                ("source", models.CharField(choices=[("jobs","Jobs"),("courses","Courses")], max_length=10)),
                ("skill", models.CharField(max_length=100)),
                ("frequency", models.IntegerField(default=0)),
            ],
            options={"ordering": ["-frequency"], "unique_together": {("run","source","skill")}},
        ),
        migrations.CreateModel(
            name="TaskRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("task_id", models.CharField(blank=True, max_length=255, unique=True)),
                ("run_name", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("PENDING","Pending"),("STARTED","Started"),("SUCCESS","Success"),("FAILURE","Failure")], default="PENDING", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
