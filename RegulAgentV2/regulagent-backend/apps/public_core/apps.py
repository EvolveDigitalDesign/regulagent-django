from django.apps import AppConfig


class PublicCoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.public_core'
    verbose_name = 'Public Core'


