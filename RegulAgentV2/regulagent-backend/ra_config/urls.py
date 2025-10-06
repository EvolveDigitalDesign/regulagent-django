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

from apps.public_core.views.well_registry import WellRegistryViewSet
from apps.public_core.views.public_facts import PublicFactsViewSet
from apps.public_core.views.public_casing_string import PublicCasingStringViewSet
from apps.public_core.views.public_perforation import PublicPerforationViewSet
from apps.public_core.views.public_well_depths import PublicWellDepthsViewSet
from apps.kernel.views.plan_preview import PlanPreviewView
from apps.tenant_overlay.views.resolved_facts import ResolvedFactsView
from apps.policy_ingest import urls as policy_urls
from apps.kernel.views.advisory import AdvisorySanityCheckView

router = DefaultRouter()
router.register(r'public/wells', WellRegistryViewSet, basename='public-wells')
router.register(r'public/facts', PublicFactsViewSet, basename='public-facts')
router.register(r'public/casing', PublicCasingStringViewSet, basename='public-casing')
router.register(r'public/perforations', PublicPerforationViewSet, basename='public-perforations')
router.register(r'public/depths', PublicWellDepthsViewSet, basename='public-depths')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
    path('api/overlay/engagements/<int:engagement_id>/resolved-facts', ResolvedFactsView.as_view()),
    path('api/plans/preview', PlanPreviewView.as_view()),
    path('api/advisory/sanity-check', AdvisorySanityCheckView.as_view()),
    path('api/policy/', include((policy_urls, 'policy_ingest'), namespace='policy')),
]
