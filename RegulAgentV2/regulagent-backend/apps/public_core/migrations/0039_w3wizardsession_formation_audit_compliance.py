from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("public_core", "0038_add_is_stale_to_extracted_document"),
    ]

    operations = [
        migrations.AddField(
            model_name="w3wizardsession",
            name="formation_audit",
            field=models.JSONField(blank=True, default=dict, help_text="FormationAuditResult from formation_isolation_auditor"),
        ),
        migrations.AddField(
            model_name="w3wizardsession",
            name="compliance_result",
            field=models.JSONField(blank=True, default=dict, help_text="ComplianceResult from coa_compliance_checker"),
        ),
        migrations.AddField(
            model_name="historicalw3wizardsession",
            name="formation_audit",
            field=models.JSONField(blank=True, default=dict, help_text="FormationAuditResult from formation_isolation_auditor"),
        ),
        migrations.AddField(
            model_name="historicalw3wizardsession",
            name="compliance_result",
            field=models.JSONField(blank=True, default=dict, help_text="ComplianceResult from coa_compliance_checker"),
        ),
    ]
