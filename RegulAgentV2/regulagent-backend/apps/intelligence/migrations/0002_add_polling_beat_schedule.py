"""
Data migration: register the poll-rrc-filing-statuses periodic task
with django-celery-beat (DatabaseScheduler).

This migration is safe to run multiple times — it uses get_or_create.
"""

from django.db import migrations


def add_beat_schedule(apps, schema_editor):
    """
    Register the RRC polling task with django-celery-beat.

    Runs every 4 hours using CrontabSchedule(minute=0, hour='*/4').
    """
    try:
        CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        # django-celery-beat may not be installed in all environments (e.g. CI)
        return

    import json

    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="*/4",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )

    PeriodicTask.objects.update_or_create(
        name="poll-rrc-filing-statuses",
        defaults={
            "task": "apps.intelligence.tasks_polling.poll_filing_statuses",
            "crontab": schedule,
            "kwargs": json.dumps({"agency": "RRC"}),
            "enabled": True,
            "description": "Poll RRC portal every 4 hours for filing status updates.",
        },
    )


def remove_beat_schedule(apps, schema_editor):
    """Reverse: delete the periodic task (leave the crontab schedule intact)."""
    try:
        PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    except LookupError:
        return
    PeriodicTask.objects.filter(name="poll-rrc-filing-statuses").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("intelligence", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_beat_schedule, reverse_code=remove_beat_schedule),
    ]
