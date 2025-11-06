"""
Django management command to set up initial tenants and users.

Based on TestDriven.io guide:
https://testdriven.io/blog/django-multi-tenant/#django-tenant-users

Usage:
    python manage.py setup_tenants
"""
from django.core.management.base import BaseCommand
from django.db import connection

from apps.tenants.models import User, Tenant, Domain
from apps.tenants.utils import create_public_tenant, provision_tenant


class Command(BaseCommand):
    help = 'Set up public tenant and sample tenant with users'

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
        else:
            # Get or create root user
            root_user, created = User.objects.get_or_create(
                email='admin@localhost',
                defaults={'is_active': True}
            )
            if created:
                root_user.set_password('admin123')
                root_user.save()
            
            # Ensure root user is in public tenant
            if root_user not in public_tenant.user_set.all():
                try:
                    public_tenant.add_user(root_user, is_superuser=True, is_staff=True)
                except Exception:
                    # User already has permissions, skip
                    pass
            
            # Update owner if needed
            if not public_tenant.owner:
                public_tenant.owner = root_user
                public_tenant.save()
            
            # Get or create domain
            public_domain, _ = Domain.objects.get_or_create(
                tenant=public_tenant,
                defaults={'domain': 'localhost', 'is_primary': True}
            )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'✓ Public tenant created: {public_tenant.schema_name} at {public_domain.domain}'
            )
        )
        
        # Create sample tenants
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
        
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('✓ Setup complete!'))
        self.stdout.write('='*60)
        self.stdout.write('\nCredentials:')
        self.stdout.write('  Root admin: admin@localhost / admin123')
        self.stdout.write('  Demo tenant: demo@example.com / demo123')
        self.stdout.write('  Test tenant: test@example.com / test123')
        self.stdout.write('\nGet JWT tokens:')
        self.stdout.write('  POST /api/auth/token/ with {"email": "demo@example.com", "password": "demo123"}')

