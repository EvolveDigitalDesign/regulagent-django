"""
Unit tests for ComponentResolver service.

Tests resolve_well_components() and build_well_geometry_from_components()
covering layer precedence, tenant isolation, lifecycle filtering, and geometry mapping.
"""
import uuid
import pytest
from decimal import Decimal

from apps.public_core.models import WellRegistry, WellComponent
from apps.public_core.services.component_resolver import (
    resolve_well_components,
    build_well_geometry_from_components,
    ResolvedComponent,
)


@pytest.fixture
def well(db):
    """Create a test WellRegistry."""
    return WellRegistry.objects.create(
        api14="42383396820000",
        state="TX",
        county="Howard",
        district="8A",
        operator_name="Test Operator",
        field_name="Test Field",
        lease_name="Test Lease",
        well_number="1",
    )


@pytest.fixture
def tenant_id():
    return uuid.uuid4()


def _casing(well, layer, top=Decimal("0.00"), bottom=Decimal("500.00"), tenant_id=None, **kwargs):
    """Helper to create a minimal casing WellComponent."""
    return WellComponent.objects.create(
        well=well,
        component_type=WellComponent.ComponentType.CASING,
        layer=layer,
        top_ft=top,
        bottom_ft=bottom,
        tenant_id=tenant_id,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. Public only
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resolve_public_only(well):
    """3 public casing components, no tenant → all 3 returned."""
    _casing(well, WellComponent.Layer.PUBLIC, top=Decimal("0.00"), bottom=Decimal("500.00"))
    _casing(well, WellComponent.Layer.PUBLIC, top=Decimal("500.00"), bottom=Decimal("1000.00"))
    _casing(well, WellComponent.Layer.PUBLIC, top=Decimal("1000.00"), bottom=Decimal("2000.00"))

    result = resolve_well_components(well)
    assert len(result) == 3
    assert all(rc.effective_layer == WellComponent.Layer.PUBLIC for rc in result)


# ---------------------------------------------------------------------------
# 2. Tenant override via supersedes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resolve_tenant_override(well, tenant_id):
    """Tenant component supersedes public one → only tenant returned for that position."""
    public_c = _casing(well, WellComponent.Layer.PUBLIC)
    tenant_c = _casing(
        well,
        WellComponent.Layer.TENANT,
        tenant_id=tenant_id,
        supersedes=public_c,
    )

    result = resolve_well_components(well, tenant_id=tenant_id)
    ids = {rc.component.id for rc in result}

    assert tenant_c.id in ids
    assert public_c.id not in ids


# ---------------------------------------------------------------------------
# 3. Plan proposed filtering
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resolve_plan_proposed(well, tenant_id):
    """include_proposed=True shows proposed states; False hides them."""
    _casing(well, WellComponent.Layer.PUBLIC)
    _casing(
        well,
        WellComponent.Layer.TENANT,
        tenant_id=tenant_id,
        top=Decimal("600.00"),
        bottom=Decimal("1200.00"),
        lifecycle_state=WellComponent.LifecycleState.PROPOSED_ADDITION,
    )
    _casing(
        well,
        WellComponent.Layer.TENANT,
        tenant_id=tenant_id,
        top=Decimal("1200.00"),
        bottom=Decimal("2000.00"),
        lifecycle_state=WellComponent.LifecycleState.PROPOSED_REMOVAL,
    )

    with_proposed = resolve_well_components(well, tenant_id=tenant_id, include_proposed=True)
    without_proposed = resolve_well_components(well, tenant_id=tenant_id, include_proposed=False)

    assert len(with_proposed) == 3
    assert len(without_proposed) == 1

    states_in = {rc.component.lifecycle_state for rc in without_proposed}
    assert WellComponent.LifecycleState.PROPOSED_ADDITION not in states_in
    assert WellComponent.LifecycleState.PROPOSED_REMOVAL not in states_in


# ---------------------------------------------------------------------------
# 4. execution_actual wins over all layers
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resolve_execution_actual_wins(well, tenant_id):
    """execution_actual takes precedence over all other layers for same position."""
    top, bottom = Decimal("0.00"), Decimal("500.00")

    _casing(well, WellComponent.Layer.PUBLIC, top=top, bottom=bottom)
    _casing(well, WellComponent.Layer.TENANT, top=top, bottom=bottom, tenant_id=tenant_id)
    exec_c = _casing(
        well,
        WellComponent.Layer.EXECUTION_ACTUAL,
        top=top,
        bottom=bottom,
        tenant_id=tenant_id,
    )

    result = resolve_well_components(well, tenant_id=tenant_id)
    assert len(result) == 1
    assert result[0].component.id == exec_c.id
    assert result[0].effective_layer == WellComponent.Layer.EXECUTION_ACTUAL


# ---------------------------------------------------------------------------
# 5. Precedence order: one component per layer at same position
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_precedence_order(well, tenant_id):
    """When all 4 layers exist at same (type, top, bottom), highest precedence wins."""
    top, bottom = Decimal("100.00"), Decimal("200.00")

    _casing(well, WellComponent.Layer.PUBLIC, top=top, bottom=bottom)
    _casing(well, WellComponent.Layer.TENANT, top=top, bottom=bottom, tenant_id=tenant_id)
    exec_c = _casing(
        well,
        WellComponent.Layer.EXECUTION_ACTUAL,
        top=top,
        bottom=bottom,
        tenant_id=tenant_id,
    )

    result = resolve_well_components(well, tenant_id=tenant_id)
    assert len(result) == 1
    assert result[0].component.id == exec_c.id


# ---------------------------------------------------------------------------
# 6. Tenant isolation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_tenant_isolation(well):
    """Two tenants — each sees their own components + public, not each other's."""
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()

    public_c = _casing(well, WellComponent.Layer.PUBLIC)
    a_c = _casing(
        well,
        WellComponent.Layer.TENANT,
        tenant_id=tid_a,
        top=Decimal("600.00"),
        bottom=Decimal("1000.00"),
    )
    b_c = _casing(
        well,
        WellComponent.Layer.TENANT,
        tenant_id=tid_b,
        top=Decimal("600.00"),
        bottom=Decimal("1000.00"),
    )

    result_a = resolve_well_components(well, tenant_id=tid_a)
    result_b = resolve_well_components(well, tenant_id=tid_b)

    ids_a = {rc.component.id for rc in result_a}
    ids_b = {rc.component.id for rc in result_b}

    assert public_c.id in ids_a
    assert a_c.id in ids_a
    assert b_c.id not in ids_a

    assert public_c.id in ids_b
    assert b_c.id in ids_b
    assert a_c.id not in ids_b


# ---------------------------------------------------------------------------
# 7. include_removed
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_include_removed(well):
    """Removed lifecycle_state hidden by default; visible with include_removed=True."""
    removed_c = _casing(
        well,
        WellComponent.Layer.PUBLIC,
        lifecycle_state=WellComponent.LifecycleState.REMOVED,
    )

    hidden = resolve_well_components(well, include_removed=False)
    visible = resolve_well_components(well, include_removed=True)

    hidden_ids = {rc.component.id for rc in hidden}
    visible_ids = {rc.component.id for rc in visible}

    assert removed_c.id not in hidden_ids
    assert removed_c.id in visible_ids


# ---------------------------------------------------------------------------
# 8. Archived excluded
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_archived_excluded(well):
    """is_archived=True components never appear regardless of flags."""
    archived_c = _casing(well, WellComponent.Layer.PUBLIC, is_archived=True)

    result_default = resolve_well_components(well)
    result_removed = resolve_well_components(well, include_removed=True)

    for result in (result_default, result_removed):
        ids = {rc.component.id for rc in result}
        assert archived_c.id not in ids


# ---------------------------------------------------------------------------
# 9. Sort order
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sort_order(well):
    """Results sorted by sort_order ascending, then top_ft ascending."""
    c1 = WellComponent.objects.create(
        well=well,
        component_type=WellComponent.ComponentType.CASING,
        layer=WellComponent.Layer.PUBLIC,
        sort_order=2,
        top_ft=Decimal("100.00"),
        bottom_ft=Decimal("200.00"),
    )
    c2 = WellComponent.objects.create(
        well=well,
        component_type=WellComponent.ComponentType.TUBING,
        layer=WellComponent.Layer.PUBLIC,
        sort_order=1,
        top_ft=Decimal("50.00"),
        bottom_ft=Decimal("150.00"),
    )
    c3 = WellComponent.objects.create(
        well=well,
        component_type=WellComponent.ComponentType.LINER,
        layer=WellComponent.Layer.PUBLIC,
        sort_order=1,
        top_ft=Decimal("200.00"),
        bottom_ft=Decimal("400.00"),
    )

    result = resolve_well_components(well)
    assert len(result) == 3

    # sort_order=1 items first; within sort_order=1, lower top_ft first
    assert result[0].component.id == c2.id  # sort_order=1, top=50
    assert result[1].component.id == c3.id  # sort_order=1, top=200
    assert result[2].component.id == c1.id  # sort_order=2, top=100


# ---------------------------------------------------------------------------
# 10. build_geometry output keys
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_build_geometry_output_keys(well):
    """Geometry dict always contains all 9 expected keys."""
    geometry = build_well_geometry_from_components(well)

    expected_keys = {
        "casing_strings",
        "formation_tops",
        "perforations",
        "production_perforations",
        "tubing",
        "liner",
        "historic_cement_jobs",
        "mechanical_equipment",
        "existing_tools",
    }
    assert set(geometry.keys()) == expected_keys


# ---------------------------------------------------------------------------
# 11. Casing mapping
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_geometry_casing_mapping(well):
    """Casing component maps to casing_strings with correct field names."""
    WellComponent.objects.create(
        well=well,
        component_type=WellComponent.ComponentType.CASING,
        layer=WellComponent.Layer.PUBLIC,
        top_ft=Decimal("0.00"),
        bottom_ft=Decimal("500.00"),
        outside_dia_in=Decimal("9.63"),
        weight_ppf=Decimal("40.00"),
        grade="J-55",
        cement_top_ft=Decimal("100.00"),
        hole_size_in=Decimal("12.25"),
        properties={"string_type": "surface"},
    )

    geometry = build_well_geometry_from_components(well)
    assert len(geometry["casing_strings"]) == 1

    entry = geometry["casing_strings"][0]
    assert entry["string_type"] == "surface"
    assert entry["outside_dia_in"] == pytest.approx(9.63)
    assert entry["weight_ppf"] == pytest.approx(40.00)
    assert entry["grade"] == "J-55"
    assert entry["top_ft"] == pytest.approx(0.0)
    assert entry["bottom_ft"] == pytest.approx(500.0)
    assert entry["cement_top_ft"] == pytest.approx(100.0)
    assert entry["hole_size_in"] == pytest.approx(12.25)


# ---------------------------------------------------------------------------
# 12. Provenance fields
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_geometry_provenance_fields(well):
    """Every geometry entry contains _component_id (str UUID) and _layer."""
    c = _casing(well, WellComponent.Layer.PUBLIC)

    geometry = build_well_geometry_from_components(well)
    entry = geometry["casing_strings"][0]

    assert "_component_id" in entry
    assert "_layer" in entry
    assert entry["_component_id"] == str(c.id)
    assert entry["_layer"] == WellComponent.Layer.PUBLIC


# ---------------------------------------------------------------------------
# 13. Perforation mapping
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_geometry_perforation_mapping(well):
    """Perforation component maps to perforations list with correct fields."""
    WellComponent.objects.create(
        well=well,
        component_type=WellComponent.ComponentType.PERFORATION,
        layer=WellComponent.Layer.PUBLIC,
        top_ft=Decimal("1200.00"),
        bottom_ft=Decimal("1250.00"),
        properties={"formation": "Spraberry", "shot_density_spf": 6},
    )

    geometry = build_well_geometry_from_components(well)
    assert len(geometry["perforations"]) == 1

    entry = geometry["perforations"][0]
    assert entry["top_ft"] == pytest.approx(1200.0)
    assert entry["bottom_ft"] == pytest.approx(1250.0)
    assert entry["formation"] == "Spraberry"
    assert entry["shot_density_spf"] == 6
    assert "_component_id" in entry
    assert "_layer" in entry


# ---------------------------------------------------------------------------
# 14. Resolve by api14 string
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resolve_by_api14_string(well):
    """Passing api14 string instead of WellRegistry instance resolves correctly."""
    _casing(well, WellComponent.Layer.PUBLIC)

    result = resolve_well_components(well.api14)
    assert len(result) == 1
    assert result[0].component.well_id == well.pk
