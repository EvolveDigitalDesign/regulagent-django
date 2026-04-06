"""
Filing Breakdown API Endpoint

GET /api/filings/breakdown/

Returns count of filings grouped by form type for dashboard chart display.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.db.models import Q, Count

from ..models import PlanSnapshot, W3FormORM


class FilingBreakdownView(APIView):
    """
    GET /api/filings/breakdown/

    Returns filing counts grouped by form type for the authenticated tenant.
    
    Response:
    {
      "breakdown": [
        {
          "form_type": "W-3A",
          "count": 42,
          "by_status": {
            "draft": 5,
            "submitted": 10,
            "approved": 27
          }
        },
        {
          "form_type": "W-3",
          "count": 38,
          "by_status": {
            "draft": 8,
            "submitted": 20,
            "approved": 10
          }
        }
      ]
    }
    """

    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request) -> Response:
        """Get filing breakdown by form type"""
        
        # Get tenant ID from request user
        tenant_id = getattr(request.user, "tenant_id", None)
        
        breakdown = []
        
        # === W-3A Filings (from PlanSnapshot) ===
        w3a_filter = Q()
        if tenant_id:
            w3a_filter &= Q(Q(visibility="public") | Q(tenant_id=tenant_id))
        else:
            w3a_filter &= Q(visibility="public")
        
        w3a_total = PlanSnapshot.objects.filter(w3a_filter).count()
        
        w3a_by_status = PlanSnapshot.objects.filter(w3a_filter).values('status').annotate(
            count=Count('id')
        ).order_by('status')
        
        w3a_status_dict = {item['status']: item['count'] for item in w3a_by_status}
        
        breakdown.append({
            "form_type": "W-3A",
            "count": w3a_total,
            "by_status": w3a_status_dict,
        })
        
        # === W-3 Filings (from W3FormORM) ===
        w3_total = W3FormORM.objects.all().count()
        
        w3_by_status = W3FormORM.objects.values('status').annotate(
            count=Count('id')
        ).order_by('status')
        
        w3_status_dict = {item['status']: item['count'] for item in w3_by_status}
        
        breakdown.append({
            "form_type": "W-3",
            "count": w3_total,
            "by_status": w3_status_dict,
        })
        
        # Additional form types (GAU, W-2, W-15, H-5)
        # These are currently placeholder data - expand as needed
        for form_type in ["GAU", "W-2", "W-15", "H-5"]:
            breakdown.append({
                "form_type": form_type,
                "count": 0,
                "by_status": {},
            })
        
        return Response({
            "breakdown": breakdown,
        }, status=status.HTTP_200_OK)





