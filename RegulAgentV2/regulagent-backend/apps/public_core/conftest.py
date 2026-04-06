import pytest

@pytest.fixture
def sample_well(db):
    """Create a sample WellRegistry entry."""
    from apps.public_core.models import WellRegistry
    return WellRegistry.objects.create(
        api14='42501705750000',
        state='TX',
        county='Andrews',
        district='08A',
        operator_name='Test Operator',
        field_name='Test Field'
    )

@pytest.fixture
def sample_plan_snapshot(db, sample_well):
    """Create a sample PlanSnapshot."""
    from apps.public_core.models import PlanSnapshot
    return PlanSnapshot.objects.create(
        well=sample_well,
        plan_id=f'{sample_well.api14}:combined',
        kind='baseline',
        status='draft',
        payload={'steps': [], 'kernel_version': '1.0'}
    )
