"""
Tests for usage tracking functionality.
"""

import pytest
from datetime import datetime, timedelta
from django.utils import timezone

from apps.tenants.models import Tenant, User, ClientWorkspace, UsageRecord
from apps.tenants.services.usage_tracker import track_usage, get_tenant_usage_summary


@pytest.mark.django_db
class TestUsageTracking:
    """Test usage tracking service and models."""

    def test_track_usage_creates_record(self, tenant, user):
        """Test that track_usage creates a usage record."""
        usage = track_usage(
            tenant=tenant,
            event_type=UsageRecord.EVENT_PLAN_GENERATED,
            resource_type='well',
            resource_id='42-123-12345-00',
            user=user,
            processing_time_ms=2500,
            metadata={'plan_type': 'W3A', 'mode': 'hybrid'}
        )

        assert usage.id is not None
        assert usage.tenant == tenant
        assert usage.user == user
        assert usage.event_type == UsageRecord.EVENT_PLAN_GENERATED
        assert usage.resource_type == 'well'
        assert usage.resource_id == '42-123-12345-00'
        assert usage.processing_time_ms == 2500
        assert usage.metadata['plan_type'] == 'W3A'

    def test_track_usage_with_workspace(self, tenant, user):
        """Test usage tracking with client workspace."""
        workspace = ClientWorkspace.objects.create(
            tenant=tenant,
            name='Test Client',
            operator_number='12345'
        )

        usage = track_usage(
            tenant=tenant,
            event_type=UsageRecord.EVENT_DOCUMENT_UPLOADED,
            resource_type='document',
            resource_id='doc-123',
            workspace=workspace,
            user=user,
        )

        assert usage.workspace == workspace
        assert usage.tenant == tenant

    def test_track_usage_with_tokens(self, tenant, user):
        """Test usage tracking for AI operations with token counts."""
        usage = track_usage(
            tenant=tenant,
            event_type=UsageRecord.EVENT_AI_CHAT_MESSAGE,
            resource_type='chat_thread',
            resource_id='thread-456',
            user=user,
            tokens_used=1500,
            metadata={'model': 'gpt-4', 'prompt_tokens': 800, 'completion_tokens': 700}
        )

        assert usage.tokens_used == 1500
        assert usage.metadata['model'] == 'gpt-4'

    def test_get_tenant_usage_summary_by_event_type(self, tenant, user):
        """Test usage summary grouped by event type."""
        # Create multiple usage records
        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_PLAN_GENERATED,
                   resource_type='well', resource_id='well-1', user=user)
        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_PLAN_GENERATED,
                   resource_type='well', resource_id='well-2', user=user)
        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_DOCUMENT_UPLOADED,
                   resource_type='document', resource_id='doc-1', user=user)

        summary = get_tenant_usage_summary(tenant=tenant, group_by='event_type')

        assert summary['total_events'] == 3
        assert len(summary['breakdown']) == 2

        # Check plan_generated has count of 2
        plan_gen = next((b for b in summary['breakdown']
                        if b['group'] == UsageRecord.EVENT_PLAN_GENERATED), None)
        assert plan_gen is not None
        assert plan_gen['count'] == 2

    def test_get_tenant_usage_summary_with_date_range(self, tenant, user):
        """Test usage summary filtered by date range."""
        now = timezone.now()
        yesterday = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        # Create records with different timestamps
        old_record = UsageRecord.objects.create(
            tenant=tenant,
            event_type=UsageRecord.EVENT_PLAN_GENERATED,
            resource_type='well',
            user=user
        )
        old_record.created_at = week_ago
        old_record.save()

        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_PLAN_GENERATED,
                   resource_type='well', resource_id='well-1', user=user)

        # Summary for last 2 days should only include recent record
        summary = get_tenant_usage_summary(
            tenant=tenant,
            start_date=yesterday,
            group_by='event_type'
        )

        assert summary['total_events'] == 1

    def test_get_tenant_usage_summary_by_workspace(self, tenant, user):
        """Test usage summary grouped by workspace."""
        workspace1 = ClientWorkspace.objects.create(
            tenant=tenant, name='Client A', operator_number='001'
        )
        workspace2 = ClientWorkspace.objects.create(
            tenant=tenant, name='Client B', operator_number='002'
        )

        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_PLAN_GENERATED,
                   workspace=workspace1, user=user)
        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_PLAN_GENERATED,
                   workspace=workspace1, user=user)
        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_DOCUMENT_UPLOADED,
                   workspace=workspace2, user=user)

        summary = get_tenant_usage_summary(tenant=tenant, group_by='workspace')

        assert summary['total_events'] == 3
        assert len(summary['breakdown']) >= 2

        # Check workspace1 has count of 2
        ws1_usage = next((b for b in summary['breakdown'] if b['group'] == 'Client A'), None)
        assert ws1_usage is not None
        assert ws1_usage['count'] == 2

    def test_get_tenant_usage_summary_with_tokens_aggregation(self, tenant, user):
        """Test that usage summary correctly aggregates token counts."""
        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_AI_CHAT_MESSAGE,
                   tokens_used=1000, user=user)
        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_AI_CHAT_MESSAGE,
                   tokens_used=1500, user=user)
        track_usage(tenant=tenant, event_type=UsageRecord.EVENT_PLAN_GENERATED,
                   tokens_used=0, user=user)

        summary = get_tenant_usage_summary(tenant=tenant, group_by='event_type')

        assert summary['total_tokens'] == 2500

        # Check AI chat messages have correct token sum
        chat_usage = next((b for b in summary['breakdown']
                          if b['group'] == UsageRecord.EVENT_AI_CHAT_MESSAGE), None)
        assert chat_usage is not None
        assert chat_usage['tokens'] == 2500


@pytest.fixture
def tenant(db):
    """Create a test tenant."""
    return Tenant.objects.create(
        name='Test Tenant',
        slug='test-tenant',
        schema_name='test_tenant'
    )


@pytest.fixture
def user(db, tenant):
    """Create a test user."""
    user = User.objects.create_user(
        email='test@example.com',
        username='testuser',
        password='testpass123'
    )
    user.tenants.add(tenant)
    return user
