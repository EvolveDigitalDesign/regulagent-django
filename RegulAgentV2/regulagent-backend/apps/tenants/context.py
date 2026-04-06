"""
Tenant context propagation via contextvars.

Provides thread-safe, async-safe tenant identity that follows execution
across sync views, async views, and Celery tasks.

Usage in HTTP middleware:
    set_current_tenant(request.tenant)

Usage in Celery tasks:
    tenant = Tenant.objects.get(id=tenant_id)
    set_current_tenant(tenant)

Reading:
    tenant = get_current_tenant()  # Returns None if not set
"""

import contextvars
from typing import Optional

_current_tenant = contextvars.ContextVar('current_tenant', default=None)


def set_current_tenant(tenant) -> None:
    """Set the current tenant for this execution context."""
    _current_tenant.set(tenant)


def get_current_tenant():
    """Get the current tenant, or None if not in a tenant context."""
    return _current_tenant.get()
