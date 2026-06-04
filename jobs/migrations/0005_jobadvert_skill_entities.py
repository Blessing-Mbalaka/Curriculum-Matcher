from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0004_jobadvert_category_jobadvert_contract_time_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobadvert",
            name="skill_entities",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
