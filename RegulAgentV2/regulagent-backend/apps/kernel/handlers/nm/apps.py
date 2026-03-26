from django.apps import AppConfig


class NmHandlerConfig(AppConfig):
    name = "apps.kernel.handlers.nm"
    label = "kernel_handler_nm"
    verbose_name = "Kernel NM Handler"

    def ready(self):
        from apps.kernel.services.jurisdiction_registry import register_handler
        from .handler import NMJurisdictionHandler

        try:
            register_handler(NMJurisdictionHandler())
        except ValueError:
            pass  # Already registered
