import pytest
from django.urls import reverse
from rest_framework import status
from django_tenants.utils import schema_context
from apps.tenants.models import ClientWorkspace


@pytest.mark.django_db
class TestClientWorkspaceAPI:
    """Test suite for ClientWorkspace API endpoints."""

    def test_create_workspace(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test creating a new workspace."""
        with schema_context(test_tenant.schema_name):
            url = reverse('client-workspaces-list')
            data = {
                'name': 'Acme Oil Co',
                'operator_number': '12345',
                'description': 'Test client workspace'
            }
            response = authenticated_tenant_client.post(url, data, format='json')

            assert response.status_code == status.HTTP_201_CREATED
            assert response.data['name'] == 'Acme Oil Co'
            assert response.data['operator_number'] == '12345'
            assert ClientWorkspace.objects.filter(name='Acme Oil Co').exists()

    def test_create_workspace_duplicate_name(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test that duplicate workspace names within a tenant are rejected."""
        with schema_context(test_tenant.schema_name):
            ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Duplicate Name'
            )

            url = reverse('client-workspaces-list')
            data = {'name': 'Duplicate Name'}
            response = authenticated_tenant_client.post(url, data, format='json')

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert 'name' in response.data

    def test_list_workspaces(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test listing all workspaces for current tenant."""
        with schema_context(test_tenant.schema_name):
            ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Client A',
                operator_number='111'
            )
            ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Client B',
                operator_number='222'
            )

            url = reverse('client-workspaces-list')
            response = authenticated_tenant_client.get(url)

            assert response.status_code == status.HTTP_200_OK
            assert len(response.data) == 2

    def test_list_workspaces_filter_active(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test filtering workspaces by is_active status."""
        with schema_context(test_tenant.schema_name):
            ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Active Workspace',
                is_active=True
            )
            ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Inactive Workspace',
                is_active=False
            )

            url = reverse('client-workspaces-list')

            # Test active filter
            response = authenticated_tenant_client.get(f'{url}?is_active=true')
            assert response.status_code == status.HTTP_200_OK
            assert len(response.data) == 1
            assert response.data[0]['name'] == 'Active Workspace'

            # Test inactive filter
            response = authenticated_tenant_client.get(f'{url}?is_active=false')
            assert response.status_code == status.HTTP_200_OK
            assert len(response.data) == 1
            assert response.data[0]['name'] == 'Inactive Workspace'

    def test_retrieve_workspace(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test retrieving a single workspace by ID."""
        with schema_context(test_tenant.schema_name):
            workspace = ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Test Workspace',
                operator_number='999'
            )

            url = reverse('client-workspaces-detail', kwargs={'pk': workspace.id})
            response = authenticated_tenant_client.get(url)

            assert response.status_code == status.HTTP_200_OK
            assert response.data['name'] == 'Test Workspace'
            assert response.data['operator_number'] == '999'

    def test_update_workspace(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test updating a workspace."""
        with schema_context(test_tenant.schema_name):
            workspace = ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Original Name'
            )

            url = reverse('client-workspaces-detail', kwargs={'pk': workspace.id})
            data = {'name': 'Updated Name', 'operator_number': '555'}
            response = authenticated_tenant_client.put(url, data, format='json')

            assert response.status_code == status.HTTP_200_OK
            workspace.refresh_from_db()
            assert workspace.name == 'Updated Name'
            assert workspace.operator_number == '555'

    def test_archive_workspace(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test archiving a workspace."""
        with schema_context(test_tenant.schema_name):
            workspace = ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='To Archive',
                is_active=True
            )

            url = reverse('client-workspaces-archive', kwargs={'pk': workspace.id})
            response = authenticated_tenant_client.post(url)

            assert response.status_code == status.HTTP_200_OK
            workspace.refresh_from_db()
            assert workspace.is_active is False

    def test_restore_workspace(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test restoring an archived workspace."""
        with schema_context(test_tenant.schema_name):
            workspace = ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='To Restore',
                is_active=False
            )

            url = reverse('client-workspaces-restore', kwargs={'pk': workspace.id})
            response = authenticated_tenant_client.post(url)

            assert response.status_code == status.HTTP_200_OK
            workspace.refresh_from_db()
            assert workspace.is_active is True

    def test_delete_workspace(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test deleting a workspace."""
        with schema_context(test_tenant.schema_name):
            workspace = ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='To Delete'
            )

            url = reverse('client-workspaces-detail', kwargs={'pk': workspace.id})
            response = authenticated_tenant_client.delete(url)

            assert response.status_code == status.HTTP_204_NO_CONTENT
            assert not ClientWorkspace.objects.filter(id=workspace.id).exists()

    def test_workspace_well_count(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test that workspace serializer includes well count."""
        from apps.public_core.models import WellRegistry

        with schema_context(test_tenant.schema_name):
            workspace = ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Workspace with Wells'
            )

            # Create some wells associated with this workspace
            WellRegistry.objects.create(
                api14='12345678901234',
                state='TX',
                workspace=workspace
            )
            WellRegistry.objects.create(
                api14='12345678901235',
                state='TX',
                workspace=workspace
            )

            url = reverse('client-workspaces-detail', kwargs={'pk': workspace.id})
            response = authenticated_tenant_client.get(url)

            assert response.status_code == status.HTTP_200_OK
            assert response.data['well_count'] == 2


@pytest.mark.django_db
class TestWellRegistryWorkspaceFiltering:
    """Test suite for workspace filtering in WellRegistry API."""

    def test_filter_wells_by_workspace(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test filtering wells by workspace ID."""
        from apps.public_core.models import WellRegistry

        with schema_context(test_tenant.schema_name):
            workspace1 = ClientWorkspace.objects.create(tenant=test_tenant, name='Workspace 1')
            workspace2 = ClientWorkspace.objects.create(tenant=test_tenant, name='Workspace 2')

            # Create wells in different workspaces
            WellRegistry.objects.create(api14='11111111111111', state='TX', workspace=workspace1)
            WellRegistry.objects.create(api14='22222222222222', state='TX', workspace=workspace1)
            WellRegistry.objects.create(api14='33333333333333', state='TX', workspace=workspace2)
            WellRegistry.objects.create(api14='44444444444444', state='TX', workspace=None)

            url = reverse('public-wells-list')

            # Filter by workspace1
            response = authenticated_tenant_client.get(f'{url}?workspace={workspace1.id}')
            assert response.status_code == status.HTTP_200_OK
            assert len(response.data) == 2

            # Filter by workspace2
            response = authenticated_tenant_client.get(f'{url}?workspace={workspace2.id}')
            assert response.status_code == status.HTTP_200_OK
            assert len(response.data) == 1

    def test_filter_wells_by_workspace_active_status(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test filtering wells by workspace active status."""
        from apps.public_core.models import WellRegistry

        with schema_context(test_tenant.schema_name):
            active_workspace = ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Active',
                is_active=True
            )
            inactive_workspace = ClientWorkspace.objects.create(
                tenant=test_tenant,
                name='Inactive',
                is_active=False
            )

            WellRegistry.objects.create(api14='11111111111111', state='TX', workspace=active_workspace)
            WellRegistry.objects.create(api14='22222222222222', state='TX', workspace=inactive_workspace)

            url = reverse('public-wells-list')

            # Filter by active workspaces
            response = authenticated_tenant_client.get(f'{url}?workspace_active=true')
            assert response.status_code == status.HTTP_200_OK
            assert len(response.data) == 1

            # Filter by inactive workspaces
            response = authenticated_tenant_client.get(f'{url}?workspace_active=false')
            assert response.status_code == status.HTTP_200_OK
            assert len(response.data) == 1

    def test_well_serializer_includes_workspace_name(self, authenticated_tenant_client, test_tenant, tenant_user):
        """Test that well serializer includes workspace name."""
        from apps.public_core.models import WellRegistry

        with schema_context(test_tenant.schema_name):
            workspace = ClientWorkspace.objects.create(tenant=test_tenant, name='Test Workspace')
            well = WellRegistry.objects.create(
                api14='12345678901234',
                state='TX',
                workspace=workspace
            )

            url = reverse('public-wells-detail', kwargs={'pk': well.id})
            response = authenticated_tenant_client.get(url)

            assert response.status_code == status.HTTP_200_OK
            assert response.data['workspace'] == workspace.id
            assert response.data['workspace_name'] == 'Test Workspace'
