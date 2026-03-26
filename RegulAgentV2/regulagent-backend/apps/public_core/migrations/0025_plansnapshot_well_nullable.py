from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Make PlanSnapshot.well nullable.

    Previously the field was NOT NULL, causing an IntegrityError when the W-3 wizard
    PDF plan importer (_import_plan_from_pdf) could not match the session's API number
    to a WellRegistry row.  Plans are still valid without a matched well record — the
    wizard session itself carries the API number — so allowing null here is safe and
    unblocks the import flow.
    """

    dependencies = [
        ("public_core", "0024_upgrade_embedding_dimensions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="plansnapshot",
            name="well",
            field=models.ForeignKey(
                blank=True,
                help_text="Well this plan belongs to. Null when the well record cannot be matched at import time.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="plan_snapshots",
                to="public_core.wellregistry",
            ),
        ),
        migrations.AlterField(
            model_name="historicalplansnapshot",
            name="well",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                help_text="Well this plan belongs to. Null when the well record cannot be matched at import time.",
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="public_core.wellregistry",
            ),
        ),
    ]
