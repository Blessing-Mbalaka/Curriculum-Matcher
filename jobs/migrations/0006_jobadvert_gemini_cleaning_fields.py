from django.db import migrations, models


def copy_description_to_raw_description(apps, schema_editor):
    JobAdvert = apps.get_model("jobs", "JobAdvert")
    for job in JobAdvert.objects.only("id", "description", "raw_description").iterator(chunk_size=200):
        if not job.raw_description:
            job.raw_description = job.description
            job.save(update_fields=["raw_description"])


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0005_jobadvert_skill_entities"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobadvert",
            name="raw_description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="jobadvert",
            name="cleaned_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="jobadvert",
            name="data_quality_flags",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="jobadvert",
            name="cleaned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(copy_description_to_raw_description, migrations.RunPython.noop),
    ]
