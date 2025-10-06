from django.apps import AppConfig


class TenantOverlayConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.tenant_overlay'
    verbose_name = 'Tenant Overlay'


