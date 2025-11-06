from django.contrib import admin
from apps.tenant_overlay.models import TenantArtifact


@admin.register(TenantArtifact)
class TenantArtifactAdmin(admin.ModelAdmin):
	list_display = ("artifact_type", "file_path", "tenant", "plan_snapshot", "extracted_document", "created_at")
	list_filter = ("artifact_type", "created_at")
	search_fields = ("file_path", "sha256")
