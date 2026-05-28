from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="JobAdvert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True)),
                ("title", models.CharField(max_length=255)),
                ("company", models.CharField(blank=True, max_length=255)),
                ("location", models.CharField(blank=True, max_length=255)),
                ("description", models.TextField()),
                ("source", models.CharField(choices=[("upload","Manual"),("adzuna","Adzuna API"),("csv","CSV Import")], default="upload", max_length=20)),
                ("external_id", models.CharField(blank=True, max_length=255, null=True)),
                ("url", models.URLField(blank=True)),
                ("salary_min", models.IntegerField(blank=True, null=True)),
                ("salary_max", models.IntegerField(blank=True, null=True)),
                ("skills_extracted", models.JSONField(blank=True, default=list)),
                ("vector", models.JSONField(blank=True, default=list)),
                ("date_posted", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
