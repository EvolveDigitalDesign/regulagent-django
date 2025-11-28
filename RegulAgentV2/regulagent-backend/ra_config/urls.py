"""
URL configuration for ra_config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from apps.public_core.views.well_registry import WellRegistryViewSet
from apps.public_core.views.public_facts import PublicFactsViewSet
from apps.public_core.views.public_casing_string import PublicCasingStringViewSet
from apps.public_core.views.public_perforation import PublicPerforationViewSet
from apps.public_core.views.public_well_depths import PublicWellDepthsViewSet
from apps.kernel.views.plan_preview import PlanPreviewView
from apps.tenant_overlay.views.resolved_facts import ResolvedFactsView
from apps.policy_ingest import urls as policy_urls
from apps.kernel.views.advisory import AdvisorySanityCheckView
from apps.public_core.views.rrc_extractions import RRCCompletionsExtractView
from apps.public_core.views.w3a_from_api import W3AFromApiView
from apps.public_core.views.plan_history import PlanHistoryView
from apps.public_core.views.plan_artifacts import PlanArtifactsView
from apps.public_core.views.artifact_download import ArtifactDownloadView
from apps.public_core.views.filing_export import FilingExportView
from apps.public_core.views.similar_wells import SimilarWellsView
from apps.public_core.views.plan_modify_ai import PlanModifyAIView
from apps.public_core.views.plan_modify import PlanModifyView
from apps.public_core.views.document_upload import DocumentUploadView
from apps.public_core.views.plan_detail import get_plan_detail
from apps.public_core.views.plan_status import (
    modify_plan,
    approve_plan,
    file_plan,
    get_plan_status,
)
from apps.public_core.views.w3_from_pna import BuildW3FromPNAView, W3HealthCheckView
from apps.tenants.views import TenantInfoView
from apps.tenant_overlay.views.tenant_wells import (
    get_well_by_api,
    bulk_get_wells,
    get_tenant_well_history,
)
from apps.tenant_overlay.views.guardrail_policy import (
    TenantGuardrailPolicyView,
    get_risk_profiles,
    validate_policy_change,
)
from apps.assistant.urls import plan_version_urls

router = DefaultRouter()
router.register(r'public/wells', WellRegistryViewSet, basename='public-wells')
router.register(r'public/facts', PublicFactsViewSet, basename='public-facts')
router.register(r'public/casing', PublicCasingStringViewSet, basename='public-casing')
router.register(r'public/perforations', PublicPerforationViewSet, basename='public-perforations')
router.register(r'public/depths', PublicWellDepthsViewSet, basename='public-depths')

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # JWT Authentication endpoints
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/token/verify/', TokenVerifyView.as_view(), name='token_verify'),
    
    # API routes
    path('api/', include(router.urls)),
    path('api/overlay/engagements/<int:engagement_id>/resolved-facts', ResolvedFactsView.as_view()),
    path('api/plans/preview', PlanPreviewView.as_view()),
    path('api/advisory/sanity-check', AdvisorySanityCheckView.as_view()),
    path('api/rrc/extractions/completions', RRCCompletionsExtractView.as_view()),
    path('api/plans/w3a/from-api', W3AFromApiView.as_view()),
    
    # W-3 Form Generation from pnaexchange
    path('api/w3/health/', W3HealthCheckView.as_view(), name='w3-health'),
    path('api/w3/build-from-pna/', BuildW3FromPNAView.as_view(), name='w3-build-from-pna'),
    
    path('api/plans/<str:api>/history', PlanHistoryView.as_view()),
    path('api/plans/<str:api>/artifacts', PlanArtifactsView.as_view()),
    path('api/artifacts/<uuid:artifact_id>/download', ArtifactDownloadView.as_view()),
    path('api/plans/<str:api>/filing/export', FilingExportView.as_view()),
    path('api/similar-wells', SimilarWellsView.as_view()),
    path('api/plans/<str:api>/modify/ai', PlanModifyAIView.as_view()),
    path('api/plans/<str:api>/modify', PlanModifyView.as_view()),
    path('api/documents/upload/', DocumentUploadView.as_view(), name='document_upload'),
    
    # Plan detail endpoint (full payload for viewing and chat interaction)
    path('api/plans/<str:plan_id>/', get_plan_detail, name='plan_detail'),
    
    # Plan status workflow endpoints
    path('api/plans/<str:plan_id>/status/', get_plan_status, name='plan_status'),
    path('api/plans/<str:plan_id>/status/modify/', modify_plan, name='plan_status_modify'),
    path('api/plans/<str:plan_id>/status/approve/', approve_plan, name='plan_status_approve'),
    path('api/plans/<str:plan_id>/status/file/', file_plan, name='plan_status_file'),
    
    # Tenant info endpoint
    path('api/tenant/', TenantInfoView.as_view(), name='tenant_info'),
    
    # Tenant wells endpoints (specific routes first, then generic)
    path('api/tenant/wells/history/', get_tenant_well_history, name='tenant_well_history'),
    path('api/tenant/wells/bulk/', bulk_get_wells, name='tenant_wells_bulk'),
    path('api/tenant/wells/<str:api14>/', get_well_by_api, name='tenant_well_by_api'),
    
    # Tenant guardrail policy endpoints
    path('api/tenant/settings/guardrails/', TenantGuardrailPolicyView.as_view(), name='tenant_guardrails'),
    path('api/tenant/settings/guardrails/risk-profiles/', get_risk_profiles, name='guardrail_profiles'),
    path('api/tenant/settings/guardrails/validate/', validate_policy_change, name='guardrail_validate'),
    
    # Chat and assistant endpoints
    path('api/chat/', include('apps.assistant.urls')),
    
    # Plan version history endpoints (from assistant app)
    path('api/plans/', include(plan_version_urls)),
    
    path('api/policy/', include((policy_urls, 'policy_ingest'), namespace='policy')),
]
