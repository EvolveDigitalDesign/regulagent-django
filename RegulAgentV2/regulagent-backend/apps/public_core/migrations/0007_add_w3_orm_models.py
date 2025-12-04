# Generated migration for W3 ORM models

from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("public_core", "0006_wellregistry_district_and_more"),
    ]

    operations = [
        # Create W3EventORM model
        migrations.CreateModel(
            name="W3EventORM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("set_cement_plug", "Set Cement Plug"), ("set_surface_plug", "Set Surface Plug"), ("set_bridge_plug", "Set Bridge Plug (CIBP)"), ("squeeze", "Squeeze Operation"), ("perforate", "Perforation"), ("tag_toc", "Tag Top of Cement"), ("tag_bridge_plug", "Tag Bridge Plug"), ("cut_casing", "Cut Casing"), ("broke_circulation", "Broke Circulation"), ("pressure_up", "Pressure Up"), ("rrc_approval", "RRC Approval"), ("other", "Other Event")], db_index=True, max_length=50)),
                ("event_date", models.DateField(db_index=True)),
                ("event_start_time", models.TimeField(blank=True, null=True)),
                ("event_end_time", models.TimeField(blank=True, null=True)),
                ("depth_top_ft", models.FloatField(blank=True, null=True)),
                ("depth_bottom_ft", models.FloatField(blank=True, null=True)),
                ("perf_depth_ft", models.FloatField(blank=True, null=True)),
                ("tagged_depth_ft", models.FloatField(blank=True, null=True)),
                ("cement_class", models.CharField(blank=True, choices=[("A", "Class A"), ("B", "Class B"), ("C", "Class C"), ("G", "Class G"), ("H", "Class H")], max_length=1, null=True)),
                ("sacks", models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ("volume_bbl", models.FloatField(blank=True, null=True)),
                ("pressure_psi", models.FloatField(blank=True, null=True)),
                ("plug_number", models.IntegerField(blank=True, null=True)),
                ("raw_event_detail", models.TextField(blank=True)),
                ("work_assignment_id", models.IntegerField(blank=True, null=True)),
                ("dwr_id", models.IntegerField(blank=True, null=True)),
                ("jump_to_next_casing", models.BooleanField(default=False)),
                ("casing_string", models.CharField(blank=True, max_length=50, null=True)),
                ("raw_input_values", models.JSONField(default=dict)),
                ("raw_transformation_rules", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("api_number", models.CharField(db_index=True, help_text="8-digit or 10-digit API number (normalized to 8-digit)", max_length=14)),
                ("well", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="w3_events", to="public_core.wellregistry")),
            ],
            options={
                "ordering": ["event_date", "event_start_time"],
            },
        ),
        # Create W3PlugORM model
        migrations.CreateModel(
            name="W3PlugORM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("api_number", models.CharField(db_index=True, max_length=14)),
                ("plug_number", models.IntegerField()),
                ("plug_type", models.CharField(choices=[("cement_plug", "Cement Plug"), ("bridge_plug", "Bridge Plug (CIBP)"), ("squeeze", "Squeeze Operation"), ("surface_plug", "Surface Plug"), ("production_plug", "Production Plug"), ("other", "Other Plug Type")], max_length=50)),
                ("operation_type", models.CharField(blank=True, choices=[("spot", "Spot (Inside Casing Only)"), ("squeeze", "Squeeze (Perforate & Squeeze into Annulus)"), ("other", "Other Operation")], max_length=50, null=True)),
                ("depth_top_ft", models.FloatField(blank=True, null=True)),
                ("depth_bottom_ft", models.FloatField(blank=True, null=True)),
                ("cement_class", models.CharField(blank=True, choices=[("A", "Class A"), ("B", "Class B"), ("C", "Class C"), ("G", "Class G"), ("H", "Class H")], max_length=1, null=True)),
                ("sacks", models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ("volume_bbl", models.FloatField(blank=True, null=True)),
                ("slurry_weight_ppg", models.FloatField(default=14.8, validators=[django.core.validators.MinValueValidator(0)])),
                ("hole_size_in", models.FloatField(blank=True, null=True)),
                ("calculated_top_of_plug_ft", models.FloatField(blank=True, null=True)),
                ("measured_top_of_plug_ft", models.FloatField(blank=True, null=True)),
                ("toc_variance_ft", models.FloatField(blank=True, null=True)),
                ("remarks", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("events", models.ManyToManyField(related_name="plugs", to="public_core.w3eventorm")),
                ("well", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="w3_plugs", to="public_core.wellregistry")),
            ],
            options={
                "ordering": ["plug_number"],
                "unique_together": {("api_number", "plug_number")},
            },
        ),
        # Create W3FormORM model
        migrations.CreateModel(
            name="W3FormORM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("api_number", models.CharField(db_index=True, max_length=14)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted", "Submitted to RRC"), ("approved", "Approved"), ("rejected", "Rejected"), ("archived", "Archived")], db_index=True, default="draft", max_length=20)),
                ("form_data", models.JSONField(help_text="Complete W-3 form JSON (header, plugs, casing_record, perforations, duqw, remarks)")),
                ("well_geometry", models.JSONField(default=dict, help_text="Casing record, existing tools, retainer tools, historic cement, KOP")),
                ("rrc_export", models.JSONField(default=list, help_text="Array of plug rows formatted for RRC submission")),
                ("validation_warnings", models.JSONField(default=list)),
                ("validation_errors", models.JSONField(default=list)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("submitted_by", models.CharField(blank=True, max_length=255, null=True)),
                ("rrc_confirmation_number", models.CharField(blank=True, max_length=255, null=True)),
                ("generated_from_w3a_snapshot", models.UUIDField(blank=True, help_text="ID of W-3A plan snapshot used to generate this form", null=True)),
                ("auto_generated", models.BooleanField(default=False, help_text="True if W-3A was auto-generated from RRC data")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("plugs", models.ManyToManyField(related_name="w3_forms", to="public_core.w3plugorm")),
                ("well", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="w3_forms", to="public_core.wellregistry")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        # Add indexes for W3EventORM
        migrations.AddIndex(
            model_name="w3eventorm",
            index=models.Index(
                fields=["api_number", "event_date"],
                name="public_core_api_numb_idx1",
            ),
        ),
        migrations.AddIndex(
            model_name="w3eventorm",
            index=models.Index(
                fields=["well", "event_date"],
                name="public_core_well_id_idx1",
            ),
        ),
        # Add indexes for W3PlugORM
        migrations.AddIndex(
            model_name="w3plugorm",
            index=models.Index(
                fields=["api_number", "plug_number"],
                name="public_core_api_numb_idx2",
            ),
        ),
        migrations.AddIndex(
            model_name="w3plugorm",
            index=models.Index(
                fields=["well", "plug_number"],
                name="public_core_well_id_idx2",
            ),
        ),
        # Add indexes for W3FormORM
        migrations.AddIndex(
            model_name="w3formorm",
            index=models.Index(
                fields=["api_number", "status"],
                name="public_core_api_numb_idx3",
            ),
        ),
        migrations.AddIndex(
            model_name="w3formorm",
            index=models.Index(
                fields=["well", "-created_at"],
                name="public_core_well_id_idx3",
            ),
        ),
    ]

