"""
Middleware to propagate tenant identity into contextvars.

Must be placed AFTER django_tenants.middleware.main.TenantMainMiddleware
in MIDDLEWARE settings so that request.tenant is already resolved.
"""

from apps.tenants.context import set_current_tenant


class TenantContextMiddleware:
    """
    Sets the current tenant contextvar from request.tenant
    (already resolved by django-tenants TenantMainMiddleware).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = getattr(request, 'tenant', None)
        if tenant:
            set_current_tenant(tenant)
        response = self.get_response(request)
        return response
