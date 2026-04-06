from django.apps import AppConfig


class TxHandlerConfig(AppConfig):
    name = "apps.kernel.handlers.tx"
    label = "kernel_handler_tx"
    verbose_name = "Kernel TX Handler"

    def ready(self):
        from apps.kernel.services.jurisdiction_registry import register_handler
        from .handler import TXJurisdictionHandler

        try:
            register_handler(TXJurisdictionHandler())
        except ValueError:
            pass  # Already registered (e.g., during test reloads)
