"""
Django management command to set up initial tenants and users.

Based on TestDriven.io guide:
https://testdriven.io/blog/django-multi-tenant/#django-tenant-users

Usage:
    python manage.py setup_tenants
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django_tenants.utils import schema_context

from apps.tenants.models import User, Tenant, Domain
from plans.signals import activate_user_plan
from apps.tenants.utils import create_public_tenant, provision_tenant
from tenant_users.permissions.models import UserTenantPermissions


class Command(BaseCommand):
    help = 'Set up public tenant and sample tenant with users'

    def add_arguments(self, parser):
        # Optional single custom tenant to provision in addition to samples
        parser.add_argument('--tenant-name', type=str, help='Name of the custom tenant to create')
        parser.add_argument('--tenant-subdomain', type=str, help='Subdomain/slug for the custom tenant')
        parser.add_argument('--schema-name', type=str, default=None, help='Schema name for the custom tenant (defaults to subdomain)')
        parser.add_argument('--owner-email', type=str, help='Email of the custom tenant owner')
        parser.add_argument('--owner-password', type=str, help='Password for the custom tenant owner')
        parser.add_argument('--skip-sample', action='store_true', help='Skip creating sample tenants')

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Setting up tenants...'))
        
        # Migrate the public schema
        self.stdout.write('Migrating public schema...')
        from django.core.management import call_command
        call_command('migrate_schemas', schema_name='public')
        
        # Create the public tenant using the tenant_users utility if it doesn't exist
        self.stdout.write(self.style.SUCCESS('Creating public tenant...'))
        public_tenant = Tenant.objects.filter(schema_name='public').first()
        
        if not public_tenant:
            public_tenant, public_domain, root_user = create_public_tenant(
                domain_url='localhost',
                owner_email='admin@localhost',
                password='admin123',
                is_superuser=True,
                is_staff=True,
            )
            # Also map 127.0.0.1 to public for local dev
            Domain.objects.get_or_create(
                tenant=public_tenant,
                domain='127.0.0.1',
                defaults={'is_primary': False}
            )
            # Ensure per-tenant admin flags within public schema
            with schema_context(public_tenant.schema_name):
                perm, _ = UserTenantPermissions.objects.get_or_create(profile=root_user)
                perm.is_staff = True
                perm.is_superuser = True
                perm.save()
        else:
            # Get or create root user
            root_user, created = User.objects.get_or_create(
                email='admin@localhost',
                defaults={'is_active': True}
            )
            # Ensure admin user is eligible for Django admin (active + verified + global staff/superuser)
            root_user.is_active = True
            # Global flags required for Django admin site access (not just tenant-level flags)
            try:
                root_user.is_staff = True
                root_user.is_superuser = True
            except Exception:
                pass
            try:
                # Some auth backends rely on verified flag
                root_user.is_verified = True
            except Exception:
                pass
            if created:
                root_user.set_password('admin123')
            root_user.save()
            # Inform django-plans that the account is fully activated
            try:
                activate_user_plan(root_user)
            except Exception:
                pass
            
            # Ensure root user is in public tenant WITH staff/superuser privileges.
            # add_user(...) updates privileges if membership already exists.
            try:
                public_tenant.add_user(root_user, is_superuser=True, is_staff=True)
            except Exception:
                # If tenant_users raises due to existing membership, ignore.
                pass
            
            # Update owner if needed
            if not public_tenant.owner:
                public_tenant.owner = root_user
                public_tenant.save()
            
            # Ensure domains for public tenant
            public_domain, _ = Domain.objects.get_or_create(
                tenant=public_tenant,
                domain='localhost',
                defaults={'is_primary': True}
            )
            Domain.objects.get_or_create(
                tenant=public_tenant,
                domain='127.0.0.1',
                defaults={'is_primary': False}
            )
            # Ensure per-tenant admin flags within public schema
            with schema_context(public_tenant.schema_name):
                perm, _ = UserTenantPermissions.objects.get_or_create(profile=root_user)
                perm.is_staff = True
                perm.is_superuser = True
                perm.save()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'✓ Public tenant created: {public_tenant.schema_name} at {public_domain.domain}'
            )
        )
        
        # Create sample tenants (unless skipped)
        sample_tenants = []
        if not options.get('skip_sample'):
            sample_tenants = [
                {
                    'name': 'Demo Company',
                    'subdomain': 'demo',
                    'schema_name': 'demo',
                    'owner': {
                        'email': 'demo@example.com',
                        'password': 'demo123',
                    },
                },
                {
                    'name': 'Test Organization',
                    'subdomain': 'test',
                    'schema_name': 'test',
                    'owner': {
                        'email': 'test@example.com',
                        'password': 'test123',
                    },
                },
            ]
        
        for tenant_data in sample_tenants:
            self.stdout.write(f'\nCreating tenant: {tenant_data["name"]}')
            
            # Get or create the tenant owner
            tenant_owner, created = User.objects.get_or_create(
                email=tenant_data['owner']['email'],
                defaults={'is_active': True}
            )
            if created:
                tenant_owner.set_password(tenant_data['owner']['password'])
                tenant_owner.is_verified = True
                tenant_owner.save()
                # Inform django-plans that the account is fully activated
                try:
                    activate_user_plan(tenant_owner)
                except Exception:
                    pass
            
            # Get or create the tenant
            tenant = Tenant.objects.filter(schema_name=tenant_data['schema_name']).first()
            if not tenant:
                tenant, domain = provision_tenant(
                    tenant_name=tenant_data['name'],
                    tenant_slug=tenant_data['subdomain'],
                    schema_name=tenant_data['schema_name'],
                    owner=tenant_owner,
                    is_superuser=True,
                    is_staff=True,
                )
            else:
                domain = Domain.objects.filter(tenant=tenant).first()
            
            # Add the root user to the tenant as well (if not already added)
            if root_user not in tenant.user_set.all():
                try:
                    tenant.add_user(
                        root_user,
                        is_superuser=True,
                        is_staff=True,
                    )
                except Exception:
                    # User already has permissions, skip
                    pass
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Tenant "{tenant.name}" created: '
                    f'{tenant.schema_name} at {domain.domain}'
                )
            )
            self.stdout.write(
                f'  Owner: {tenant_owner.email} (password: {tenant_data["owner"]["password"]})'
            )
        
        # Optionally create a single custom tenant from CLI args
        if any(options.get(k) for k in ('tenant_name', 'tenant_subdomain', 'owner_email', 'owner_password')):
            missing = [k for k in ('tenant_name', 'tenant_subdomain', 'owner_email', 'owner_password') if not options.get(k)]
            if missing:
                self.stdout.write(self.style.ERROR(
                    'Missing required arguments for custom tenant: ' + ', '.join(missing)
                ))
            else:
                self.stdout.write(f'\nCreating custom tenant: {options["tenant_name"]}')
                schema_name = options.get('schema_name') or options['tenant_subdomain']
                
                # Get or create the tenant owner
                tenant_owner, created = User.objects.get_or_create(
                    email=options['owner_email'],
                    defaults={'is_active': True}
                )
                if created:
                    tenant_owner.set_password(options['owner_password'])
                    tenant_owner.is_verified = True
                    tenant_owner.save()
                    try:
                        activate_user_plan(tenant_owner)
                    except Exception:
                        pass
                
                # Get or create the tenant
                tenant = Tenant.objects.filter(schema_name=schema_name).first()
                if not tenant:
                    tenant, domain = provision_tenant(
                        tenant_name=options['tenant_name'],
                        tenant_slug=options['tenant_subdomain'],
                        schema_name=schema_name,
                        owner=tenant_owner,
                        is_superuser=True,
                        is_staff=True,
                    )
                else:
                    domain = Domain.objects.filter(tenant=tenant).first()
                
                # Add the root user to the tenant as well (if not already added)
                if root_user not in tenant.user_set.all():
                    try:
                        tenant.add_user(
                            root_user,
                            is_superuser=True,
                            is_staff=True,
                        )
                    except Exception:
                        pass
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✓ Tenant "{tenant.name}" created: '
                        f'{tenant.schema_name} at {domain.domain if domain else "N/A"}'
                    )
                )
                self.stdout.write(
                    f'  Owner: {tenant_owner.email} (password: {options["owner_password"]})'
                )
        
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('✓ Setup complete!'))
        self.stdout.write('='*60)
        self.stdout.write('\nCredentials:')
        self.stdout.write('  Root admin: admin@localhost / admin123')
        if not options.get('skip_sample'):
            self.stdout.write('  Demo tenant: demo@example.com / demo123')
            self.stdout.write('  Test tenant: test@example.com / test123')
        if options.get('tenant_name') and options.get('owner_email') and options.get('owner_password'):
            self.stdout.write(f'  {options["tenant_name"]}: {options["owner_email"]} / {options["owner_password"]}')
        self.stdout.write('\nGet JWT tokens:')
        self.stdout.write('  POST /api/auth/token/ with {"email": "demo@example.com", "password": "demo123"}')

