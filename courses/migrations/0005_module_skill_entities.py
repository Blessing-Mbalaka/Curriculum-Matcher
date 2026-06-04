from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0004_course_country_course_university_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="module",
            name="skill_entities",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
