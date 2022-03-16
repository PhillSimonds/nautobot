# Generated by Django 3.1.14 on 2022-03-16 15:25

from django.db import migrations, models
import nautobot.extras.utils


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("extras", "0029_dynamicgroup"),
    ]

    operations = [
        migrations.AddField(
            model_name="tag",
            name="content_types",
            field=models.ManyToManyField(
                blank=True,
                limit_choices_to=nautobot.extras.utils.PrimaryModelRelatedContentType(),
                null=True,
                related_name="tags",
                to="contenttypes.ContentType",
            ),
        ),
    ]
