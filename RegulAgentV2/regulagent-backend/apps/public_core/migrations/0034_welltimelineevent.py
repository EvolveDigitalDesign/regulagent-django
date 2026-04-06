import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('public_core', '0033_extracteddocument_segment'),
    ]

    operations = [
        migrations.CreateModel(
            name='WellTimelineEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_date', models.DateField(blank=True, null=True)),
                ('event_date_precision', models.CharField(choices=[('day', 'Day'), ('month', 'Month'), ('year', 'Year'), ('unknown', 'Unknown')], default='unknown', max_length=8)),
                ('event_type', models.CharField(choices=[('drilling', 'Drilling'), ('completion', 'Completion'), ('workover', 'Workover'), ('recompletion', 'Recompletion'), ('plugging', 'Plugging'), ('cement_job', 'Cement Job'), ('permit', 'Permit'), ('plugging_proposal', 'Plugging Proposal'), ('test', 'Test'), ('other', 'Other')], max_length=32)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, default='')),
                ('key_data', models.JSONField(blank=True, default=dict)),
                ('source_document_type', models.CharField(blank=True, default='', max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('well', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='timeline_events', to='public_core.wellregistry')),
                ('source_document', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='timeline_events', to='public_core.extracteddocument')),
                ('source_segment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='timeline_events', to='public_core.documentsegment')),
                ('components_installed', models.ManyToManyField(blank=True, related_name='installed_by_events', to='public_core.wellcomponent')),
                ('components_removed', models.ManyToManyField(blank=True, related_name='removed_by_events', to='public_core.wellcomponent')),
            ],
            options={
                'db_table': 'public_core_well_timeline_event',
                'ordering': ['event_date', 'created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='welltimelineevent',
            index=models.Index(fields=['well', 'event_date'], name='timeline_well_date_idx'),
        ),
        migrations.AddIndex(
            model_name='welltimelineevent',
            index=models.Index(fields=['well', 'event_type'], name='timeline_well_type_idx'),
        ),
    ]
