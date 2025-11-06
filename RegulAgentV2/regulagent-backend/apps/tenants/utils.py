"""
Utility functions for tenant and user provisioning.

Based on TestDriven.io guide:
https://testdriven.io/blog/django-multi-tenant/#django-tenant-users
"""
from typing import Tuple

from django.db import transaction
from tenant_users.tenants.utils import create_public_tenant as tenant_users_create_public_tenant

from apps.tenants.models import Tenant, Domain, User


def create_public_tenant(
    domain_url: str = "localhost",
    owner_email: str = "admin@localhost",
    **kwargs
) -> Tuple[Tenant, Domain]:
    """
    Create the public (shared) tenant with a root user.
    
    This should be run once during initial setup.
    
    Args:
        domain_url: Domain for the public tenant (default: "localhost")
        owner_email: Email for the root superuser
        **kwargs: Additional user fields (username, password, etc.)
    
    Returns:
        Tuple of (Tenant, Domain)
    """
    return tenant_users_create_public_tenant(
        domain_url=domain_url,
        owner_email=owner_email,
        **kwargs
    )


def provision_tenant(
    tenant_name: str,
    tenant_slug: str,
    schema_name: str,
    owner: User,
    is_superuser: bool = False,
    is_staff: bool = False,
) -> Tuple[Tenant, Domain]:
    """
    Provision a new tenant with its own schema and domain.
    
    Args:
        tenant_name: Human-readable name for the tenant
        tenant_slug: URL-safe slug for the tenant
        schema_name: PostgreSQL schema name (should match slug)
        owner: User instance who will own this tenant
        is_superuser: Whether owner has superuser permissions in this tenant
        is_staff: Whether owner has staff permissions in this tenant
    
    Returns:
        Tuple of (Tenant, Domain)
    
    Example:
        >>> user = User.objects.get(email='user@example.com')
        >>> tenant, domain = provision_tenant(
        ...     tenant_name="Acme Corp",
        ...     tenant_slug="acme",
        ...     schema_name="acme",
        ...     owner=user,
        ...     is_staff=True
        ... )
    """
    with transaction.atomic():
        # Create the tenant
        tenant = Tenant.objects.create(
            name=tenant_name,
            slug=tenant_slug,
            schema_name=schema_name,
            owner=owner,
        )
        
        # Create the domain (subdomain routing)
        domain = Domain.objects.create(
            domain=f"{tenant_slug}.localhost",  # Adjust for production
            tenant=tenant,
            is_primary=True,
        )
        
        # Add the owner to the tenant with appropriate permissions (if not already)
        if owner not in tenant.user_set.all():
            try:
                tenant.add_user(
                    owner,
                    is_superuser=is_superuser,
                    is_staff=is_staff,
                )
            except Exception:
                # User already has permissions in this tenant, skip
                pass
        
        return tenant, domain


def delete_tenant(tenant: Tenant, force: bool = False):
    """
    Delete a tenant and its schema.
    
    Args:
        tenant: Tenant instance to delete
        force: If True, skip safety checks
    
    Warning:
        This will permanently delete all data in the tenant's schema!
    """
    if not force:
        # Add safety checks here if needed
        pass
    
    tenant.delete(force_drop=True)


def add_user_to_tenant(
    user: User,
    tenant: Tenant,
    is_superuser: bool = False,
    is_staff: bool = False,
):
    """
    Add an existing user to a tenant with specific permissions.
    
    Args:
        user: User instance to add
        tenant: Tenant to add the user to
        is_superuser: Whether user has superuser permissions in this tenant
        is_staff: Whether user has staff permissions in this tenant
    """
    tenant.add_user(user, is_superuser=is_superuser, is_staff=is_staff)


def remove_user_from_tenant(user: User, tenant: Tenant):
    """
    Remove a user from a tenant.
    
    Args:
        user: User instance to remove
        tenant: Tenant to remove the user from
    
    Raises:
        ValidationError: If trying to remove the tenant owner
    """
    if tenant.owner == user:
        from django.core.exceptions import ValidationError
        raise ValidationError("Cannot remove the tenant owner from the tenant")
    
    tenant.remove_user(user)

