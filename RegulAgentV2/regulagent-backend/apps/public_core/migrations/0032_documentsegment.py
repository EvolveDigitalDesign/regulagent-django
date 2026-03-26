import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('public_core', '0031_add_triage_confidence_data_status'),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentSegment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('api_number', models.CharField(db_index=True, max_length=16)),
                ('source_filename', models.CharField(db_index=True, max_length=255)),
                ('source_path', models.TextField(blank=True, default='')),
                ('file_hash', models.CharField(blank=True, default='', max_length=64)),
                ('source_type', models.CharField(choices=[('neubus', 'Neubus'), ('nm_ocd', 'NM OCD'), ('upload', 'Upload')], max_length=16)),
                ('page_start', models.PositiveIntegerField()),
                ('page_end', models.PositiveIntegerField()),
                ('total_source_pages', models.PositiveIntegerField(default=0)),
                ('form_type', models.CharField(db_index=True, max_length=32)),
                ('classification_method', models.CharField(choices=[('text', 'Text'), ('vision', 'Vision'), ('filename', 'Filename'), ('hybrid', 'Hybrid')], max_length=16)),
                ('classification_confidence', models.CharField(choices=[('high', 'High'), ('medium', 'Medium'), ('low', 'Low'), ('none', 'None')], max_length=8)),
                ('classification_evidence', models.TextField(blank=True, default='')),
                ('tags', models.JSONField(blank=True, default=list)),
                ('status', models.CharField(choices=[('classified', 'Classified'), ('extracting', 'Extracting'), ('extracted', 'Extracted'), ('error', 'Error')], default='classified', max_length=16)),
                ('raw_text_cache', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('well', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='document_segments', to='public_core.wellregistry')),
                ('extracted_document', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='source_segment', to='public_core.extracteddocument')),
            ],
            options={
                'db_table': 'public_core_document_segment',
                'ordering': ['source_filename', 'page_start'],
            },
        ),
        migrations.AddIndex(
            model_name='documentsegment',
            index=models.Index(fields=['api_number', 'form_type'], name='docseg_api_formtype_idx'),
        ),
        migrations.AddIndex(
            model_name='documentsegment',
            index=models.Index(fields=['source_filename', 'page_start'], name='docseg_file_page_idx'),
        ),
        migrations.AddIndex(
            model_name='documentsegment',
            index=models.Index(fields=['status'], name='docseg_status_idx'),
        ),
        migrations.AddIndex(
            model_name='documentsegment',
            index=models.Index(fields=['well', 'form_type'], name='docseg_well_formtype_idx'),
        ),
    ]
