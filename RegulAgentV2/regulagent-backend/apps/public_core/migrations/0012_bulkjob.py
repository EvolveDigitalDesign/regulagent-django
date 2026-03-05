# Generated migration for BulkJob model

from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('public_core', '0011_extracteddocument_public_core_api_num_685bef_idx'),
    ]

    operations = [
        migrations.CreateModel(
            name='BulkJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('tenant_id', models.UUIDField(db_index=True, help_text='Tenant who owns this job')),
                ('job_type', models.CharField(choices=[('generate_plans', 'Generate Plans'), ('update_status', 'Update Status'), ('export_data', 'Export Data')], db_index=True, help_text='Type of bulk operation', max_length=50)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('processing', 'Processing'), ('completed', 'Completed'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], db_index=True, default='queued', help_text='Current job status', max_length=20)),
                ('total_items', models.IntegerField(default=0, help_text='Total number of items to process')),
                ('processed_items', models.IntegerField(default=0, help_text='Number of items successfully processed')),
                ('failed_items', models.IntegerField(default=0, help_text='Number of items that failed')),
                ('input_data', models.JSONField(default=dict, help_text='Input parameters for the job (e.g., well_ids, options)')),
                ('result_data', models.JSONField(default=dict, help_text='Results of the operation (e.g., created plan IDs, error details)')),
                ('error_message', models.TextField(blank=True, help_text='Error message if job failed')),
                ('created_by', models.EmailField(help_text='User who initiated the job', max_length=254)),
                ('celery_task_id', models.CharField(blank=True, help_text='Celery task ID for tracking', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'public_core_bulk_jobs',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='bulkjob',
            index=models.Index(fields=['tenant_id', 'status'], name='public_core_tenant__7b5cfa_idx'),
        ),
        migrations.AddIndex(
            model_name='bulkjob',
            index=models.Index(fields=['tenant_id', 'created_at'], name='public_core_tenant__19e3a5_idx'),
        ),
        migrations.AddIndex(
            model_name='bulkjob',
            index=models.Index(fields=['job_type', 'status'], name='public_core_job_typ_8d4e51_idx'),
        ),
        migrations.AddIndex(
            model_name='bulkjob',
            index=models.Index(fields=['celery_task_id'], name='public_core_celery__f2b8c0_idx'),
        ),
    ]
