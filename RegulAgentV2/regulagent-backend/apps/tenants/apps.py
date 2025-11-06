from django.apps import AppConfig


class TenantsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.tenants'
    
    def ready(self):
        """Import signals when the app is ready."""
        from apps.tenants import signals  # noqa: F401


