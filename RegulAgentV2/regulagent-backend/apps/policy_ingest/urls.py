from django.urls import path
from .views import PolicySectionsListView, PolicyRulesListView


urlpatterns = [
    path('sections/', PolicySectionsListView.as_view(), name='policy-sections'),
    path('rules/', PolicyRulesListView.as_view(), name='policy-rules'),
]


