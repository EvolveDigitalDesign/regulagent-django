"""
API endpoints for querying and managing C-103 form data.

Provides REST endpoints for:
- List/retrieve C-103 forms
- Create/update C-103 forms
- Submit C-103 forms to NMOCD
- List/retrieve C-103 plugs (nested under forms)
- List/retrieve C-103 events
- Filter by API number, form type, status, region, etc.
"""

from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.public_core.models import C103EventORM, C103PlugORM, C103FormORM
from apps.public_core.serializers.c103_serializers import (
    C103EventSerializer,
    C103EventCreateUpdateSerializer,
    C103PlugSerializer,
    C103PlugCreateUpdateSerializer,
    C103FormListSerializer,
    C103FormDetailSerializer,
    C103FormCreateUpdateSerializer,
    C103FormSubmitSerializer,
    DWRUploadSerializer,
    SubsequentReportSerializer,
)

logger = logging.getLogger(__name__)


class C103FormViewSet(viewsets.ModelViewSet):
    """
    ViewSet for C-103 forms.

    Endpoints:
    - GET  /api/c103/forms/                          - List forms
    - POST /api/c103/forms/                          - Create form
    - GET  /api/c103/forms/{id}/                     - Retrieve form
    - PUT  /api/c103/forms/{id}/                     - Update form
    - PATCH /api/c103/forms/{id}/                    - Partial update
    - DELETE /api/c103/forms/{id}/                   - Delete form
    - POST /api/c103/forms/{id}/submit/              - Submit form for filing
    - GET  /api/c103/forms/by-api/{api_number}/      - List forms for API number
    - GET  /api/c103/forms/pending-submission/       - List forms pending submission
    - GET  /api/c103/forms/{id}/export-pdf/          - Export as PDF (routes by lease_type)
    - GET  /api/c103/forms/{id}/export-sundry-pdf/   - Export as BLM Sundry 3160-5 PDF (forced)
    """

    queryset = C103FormORM.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        """Choose serializer based on action."""
        if self.action == 'list':
            return C103FormListSerializer
        elif self.action == 'retrieve':
            return C103FormDetailSerializer
        elif self.action == 'submit':
            return C103FormSubmitSerializer
        elif self.action in ('create', 'update', 'partial_update'):
            return C103FormCreateUpdateSerializer
        return C103FormDetailSerializer

    def get_queryset(self):
        """Filter queryset by query parameters."""
        queryset = super().get_queryset()

        # Tenant isolation — match the workspace/tenant_id pattern from the model
        workspace_id = self.request.query_params.get('workspace_id')
        if workspace_id:
            queryset = queryset.filter(workspace_id=workspace_id)

        tenant_id = self.request.query_params.get('tenant_id')
        if tenant_id:
            queryset = queryset.filter(tenant_id=tenant_id)

        # Filter by API number
        api_number = self.request.query_params.get('api_number')
        if api_number:
            queryset = queryset.filter(api_number=api_number)

        # Filter by form type (noi / subsequent)
        form_type = self.request.query_params.get('form_type')
        if form_type:
            queryset = queryset.filter(form_type=form_type)

        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filter by region
        region = self.request.query_params.get('region')
        if region:
            queryset = queryset.filter(region=region)

        # Filter by well
        well_id = self.request.query_params.get('well_id')
        if well_id:
            queryset = queryset.filter(well_id=well_id)

        return queryset.order_by('-created_at')

    @action(detail=False, methods=['get'])
    def by_api(self, request):
        """Get all C-103 forms for a specific API number."""
        api_number = request.query_params.get('api_number')
        if not api_number:
            return Response(
                {'error': 'api_number query parameter required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        forms = self.get_queryset().filter(api_number=api_number)
        serializer = C103FormListSerializer(forms, many=True)
        return Response({
            'api_number': api_number,
            'count': forms.count(),
            'forms': serializer.data,
        })

    @action(detail=False, methods=['get'])
    def pending_submission(self, request):
        """Get all C-103 forms pending submission."""
        forms = self.get_queryset().filter(
            status__in=['draft', 'internal_review', 'engineer_approved']
        )
        serializer = C103FormListSerializer(forms, many=True)
        return Response({
            'count': forms.count(),
            'forms': serializer.data,
        })

    @action(detail=False, methods=['get'])
    def filed(self, request):
        """Get all filed/approved C-103 forms."""
        forms = self.get_queryset().filter(
            status__in=['filed', 'agency_approved']
        )
        serializer = C103FormListSerializer(forms, many=True)
        return Response({
            'count': forms.count(),
            'forms': serializer.data,
        })

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """Submit a C-103 form for filing — transitions status to 'filed'."""
        form = self.get_object()

        if form.status not in ('draft', 'internal_review', 'engineer_approved'):
            return Response(
                {'error': f'Cannot submit form with status: {form.get_status_display()}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        form.mark_filed(
            submitted_by=serializer.validated_data['submitted_by'],
            nmocd_confirmation_number=serializer.validated_data.get('nmocd_confirmation_number'),
        )

        return Response({
            'success': True,
            'message': 'C-103 form filed with NMOCD',
            'submitted_at': form.submitted_at,
            'nmocd_confirmation_number': form.nmocd_confirmation_number,
        })

    @staticmethod
    def _build_c103_form_data(form):
        """Build the c103_form_data dict from a C103FormORM instance.

        Header fields are sourced first from the ORM record's plan_data (richer),
        falling back to direct ORM fields where they exist.
        """
        # Seed from plan_data header if present
        plan_data = form.plan_data or {}
        plan_header = plan_data.get("header", {})

        def _get(orm_attr, plan_key=None):
            """Return plan_data header value if non-empty, else ORM attr."""
            key = plan_key or orm_attr
            plan_val = plan_header.get(key)
            if plan_val:
                return plan_val
            return getattr(form, orm_attr, '') or ''

        c103_form_data = {
            "header": {
                "api_number":       _get("api_number"),
                "well_name":        plan_header.get("well_name", ''),
                "well_number":      plan_header.get("well_number", ''),
                "operator":         plan_header.get("operator", ''),
                "operator_address": plan_header.get("operator_address", ''),
                "phone":            plan_header.get("phone", ''),
                "field_pool":       plan_header.get("field_pool", ''),
                "location":         plan_header.get("location", ''),
                "county":           plan_header.get("county", ''),
                "state":            plan_header.get("state", ''),
                "lease_serial":     plan_header.get("lease_serial", ''),
                "well_type":        plan_header.get("well_type", ''),
                "lease_type":       _get("lease_type"),
            },
            "submission_type": plan_data.get("submission_type") or form.form_type or "notice_of_intent",
            "action_type":     plan_data.get("action_type") or "plug_abandon",
            "remarks":         plan_data.get("remarks") or "",
            "certification":   plan_data.get("certification") or {},
        }

        # Pull additional optional top-level fields from plan_data
        for field in ("indian_tribe", "ca_agreement"):
            if plan_data.get(field):
                c103_form_data[field] = plan_data[field]

        return c103_form_data

    @action(detail=True, methods=['get'], url_path='export-pdf')
    def export_pdf(self, request, pk=None):
        """Export C-103 form as PDF.

        Routes to BLM Sundry 3160-5 PDF if lease_type is 'federal' or 'indian',
        otherwise returns 501 (C-103 PDF generator not yet implemented).
        """
        form = self.get_object()

        # Federal/Indian wells → BLM Sundry 3160-5
        if form.lease_type in ('federal', 'indian'):
            try:
                from apps.public_core.services.sundry_pdf_generator import generate_sundry_pdf
                import os
                from django.http import FileResponse

                c103_form_data = self._build_c103_form_data(form)
                result = generate_sundry_pdf(c103_form_data)

                file_path = result["temp_path"]
                filename = os.path.basename(file_path)
                response = FileResponse(
                    open(file_path, 'rb'),
                    content_type='application/pdf',
                )
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response

            except Exception as exc:
                logger.exception("export_pdf: Sundry PDF generation failed: %s", exc)
                return Response(
                    {'error': 'PDF generation failed', 'detail': str(exc)},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        # State/fee wells → C-103 PDF (not yet implemented)
        return Response(
            {'detail': 'C-103 PDF generation not yet implemented for state/fee wells'},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    @action(detail=True, methods=['get'], url_path='export-sundry-pdf')
    def export_sundry_pdf(self, request, pk=None):
        """Export C-103 form as BLM Sundry 3160-5 PDF (explicit endpoint).

        Forces Sundry PDF output regardless of lease_type.
        Useful for testing or when the well record's lease_type is incorrect.
        """
        form = self.get_object()

        try:
            from apps.public_core.services.sundry_pdf_generator import generate_sundry_pdf
            import os
            from django.http import FileResponse

            c103_form_data = self._build_c103_form_data(form)
            result = generate_sundry_pdf(c103_form_data)

            file_path = result["temp_path"]
            filename = os.path.basename(file_path)
            response = FileResponse(
                open(file_path, 'rb'),
                content_type='application/pdf',
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response

        except Exception as exc:
            logger.exception("export_sundry_pdf: generation failed: %s", exc)
            return Response(
                {'error': 'BLM Sundry PDF generation failed', 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['post'], url_path='upload-dwr')
    def upload_dwr(self, request, pk=None):
        """Upload DWR PDF(s) for a filed NOI.

        Accepts multipart/form-data with 'files' field.
        Parses DWRs and returns preview data (not yet committed).

        Returns:
            SubsequentReportData serialized as JSON preview.
        """
        noi_form = self.get_object()

        serializer = DWRUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        uploaded_files = serializer.validated_data['files']

        # Write uploaded files to a temp directory for the parser
        import os
        import tempfile

        tmp_paths = []
        tmp_dir = tempfile.mkdtemp(prefix="dwr_upload_")
        try:
            for f in uploaded_files:
                tmp_path = os.path.join(tmp_dir, f.name)
                with open(tmp_path, 'wb') as fh:
                    for chunk in f.chunks():
                        fh.write(chunk)
                tmp_paths.append(tmp_path)

            from apps.public_core.services.subsequent_report_generator import SubsequentReportGenerator
            generator = SubsequentReportGenerator()
            report_data = generator.generate_from_uploaded_pdfs(noi_form, tmp_paths)
        except Exception as exc:
            logger.exception("upload_dwr: generation failed: %s", exc)
            return Response(
                {'error': 'DWR parsing failed', 'detail': str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        finally:
            # Clean up temp files
            for path in tmp_paths:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

        response_data = {
            'api_number': report_data.api_number,
            'noi_form_id': report_data.noi_form_id,
            'daily_summaries': report_data.daily_summaries,
            'actual_plugs': report_data.actual_plugs,
            'reconciliation': report_data.reconciliation,
            'operations_narrative': report_data.operations_narrative,
            'total_days': report_data.total_days,
            'start_date': report_data.start_date.isoformat() if report_data.start_date else None,
            'end_date': report_data.end_date.isoformat() if report_data.end_date else None,
        }

        out_serializer = SubsequentReportSerializer(data=response_data)
        out_serializer.is_valid()  # read-only fields; always valid
        return Response(out_serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='subsequent-report')
    def subsequent_report(self, request, pk=None):
        """Generate subsequent report from uploaded DWR data.

        Accepts either:
        - JSON body with 'dwr_data' key (preview dict from upload-dwr), OR
        - Multipart upload with 'files' field (parse-then-commit in one step).

        Creates a new C103FormORM with form_type='subsequent'.

        Returns:
            SubsequentReportData + subsequent_form_id of the persisted form.
        """
        noi_form = self.get_object()

        from apps.public_core.services.subsequent_report_generator import (
            SubsequentReportGenerator,
            SubsequentReportData,
        )
        generator = SubsequentReportGenerator()

        # Branch A: pre-parsed dwr_data provided in JSON body
        dwr_data = request.data.get('dwr_data') if hasattr(request.data, 'get') else None

        if dwr_data is not None:
            # Reconstruct SubsequentReportData from the client-provided preview
            try:
                report_data = SubsequentReportData(
                    api_number=dwr_data.get('api_number', noi_form.api_number),
                    noi_form_id=noi_form.id,
                    daily_summaries=dwr_data.get('daily_summaries', []),
                    actual_plugs=dwr_data.get('actual_plugs', []),
                    reconciliation=dwr_data.get('reconciliation'),
                    operations_narrative=dwr_data.get('operations_narrative', ''),
                    total_days=dwr_data.get('total_days', 0),
                )
                raw_start = dwr_data.get('start_date')
                raw_end = dwr_data.get('end_date')
                if raw_start:
                    from datetime import date as _date
                    report_data.start_date = _date.fromisoformat(raw_start)
                if raw_end:
                    from datetime import date as _date
                    report_data.end_date = _date.fromisoformat(raw_end)
            except Exception as exc:
                logger.exception("subsequent_report: invalid dwr_data: %s", exc)
                return Response(
                    {'error': 'Invalid dwr_data payload', 'detail': str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        else:
            # Branch B: direct file upload (parse + commit atomically)
            file_serializer = DWRUploadSerializer(data=request.data)
            if not file_serializer.is_valid():
                return Response(file_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            import os
            import tempfile

            uploaded_files = file_serializer.validated_data['files']
            tmp_paths = []
            tmp_dir = tempfile.mkdtemp(prefix="dwr_subsequent_")
            try:
                for f in uploaded_files:
                    tmp_path = os.path.join(tmp_dir, f.name)
                    with open(tmp_path, 'wb') as fh:
                        for chunk in f.chunks():
                            fh.write(chunk)
                    tmp_paths.append(tmp_path)

                report_data = generator.generate_from_uploaded_pdfs(noi_form, tmp_paths)
            except Exception as exc:
                logger.exception("subsequent_report: DWR generation failed: %s", exc)
                return Response(
                    {'error': 'DWR parsing failed', 'detail': str(exc)},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            finally:
                for path in tmp_paths:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                try:
                    os.rmdir(tmp_dir)
                except OSError:
                    pass

        # Persist the subsequent form
        try:
            subsequent_form = generator.create_subsequent_form(noi_form, report_data)
        except Exception as exc:
            logger.exception("subsequent_report: create_subsequent_form failed: %s", exc)
            return Response(
                {'error': 'Failed to create subsequent report form', 'detail': str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_data = {
            'api_number': report_data.api_number,
            'noi_form_id': report_data.noi_form_id,
            'daily_summaries': report_data.daily_summaries,
            'actual_plugs': report_data.actual_plugs,
            'reconciliation': report_data.reconciliation,
            'operations_narrative': report_data.operations_narrative,
            'total_days': report_data.total_days,
            'start_date': report_data.start_date.isoformat() if report_data.start_date else None,
            'end_date': report_data.end_date.isoformat() if report_data.end_date else None,
            'subsequent_form_id': subsequent_form.id,
        }

        out_serializer = SubsequentReportSerializer(data=response_data)
        out_serializer.is_valid()
        return Response(out_serializer.data, status=status.HTTP_201_CREATED)


class C103PlugViewSet(viewsets.ModelViewSet):
    """
    ViewSet for C-103 plugs — scoped to a C103Form via form_pk.

    Endpoints (nested under /api/c103/forms/{form_pk}/):
    - GET  plugs/          - List plugs for form
    - POST plugs/          - Create plug for form
    - GET  plugs/{id}/     - Retrieve plug
    - PUT  plugs/{id}/     - Update plug
    - PATCH plugs/{id}/    - Partial update
    - DELETE plugs/{id}/   - Delete plug
    """

    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return C103PlugCreateUpdateSerializer
        return C103PlugSerializer

    def get_queryset(self):
        form_id = self.kwargs.get('form_pk')
        if form_id:
            return C103PlugORM.objects.filter(c103_form_id=form_id).order_by('plug_number')
        return C103PlugORM.objects.none()

    def perform_create(self, serializer):
        form_id = self.kwargs.get('form_pk')
        serializer.save(c103_form_id=form_id)


class C103EventViewSet(viewsets.ModelViewSet):
    """
    ViewSet for C-103 events.

    Endpoints:
    - GET  /api/c103/events/          - List events (with optional filtering)
    - POST /api/c103/events/          - Create event
    - GET  /api/c103/events/{id}/     - Retrieve event
    - PUT  /api/c103/events/{id}/     - Update event
    - PATCH /api/c103/events/{id}/    - Partial update
    - DELETE /api/c103/events/{id}/   - Delete event
    - GET  /api/c103/events/by-api/   - List events for API number
    """

    queryset = C103EventORM.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return C103EventCreateUpdateSerializer
        return C103EventSerializer

    def get_queryset(self):
        """Filter queryset by query parameters."""
        queryset = super().get_queryset()

        # Filter by API number
        api_number = self.request.query_params.get('api_number')
        if api_number:
            queryset = queryset.filter(api_number=api_number)

        # Filter by event type
        event_type = self.request.query_params.get('event_type')
        if event_type:
            queryset = queryset.filter(event_type=event_type)

        # Filter by parent form
        form_id = self.request.query_params.get('c103_form')
        if form_id:
            queryset = queryset.filter(c103_form_id=form_id)

        # Filter by well
        well_id = self.request.query_params.get('well_id')
        if well_id:
            queryset = queryset.filter(well_id=well_id)

        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(event_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(event_date__lte=date_to)

        return queryset.order_by('event_date', 'event_start_time')

    @action(detail=False, methods=['get'])
    def by_api(self, request):
        """Get all C-103 events for a specific API number."""
        api_number = request.query_params.get('api_number')
        if not api_number:
            return Response(
                {'error': 'api_number query parameter required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        events = self.get_queryset().filter(api_number=api_number)
        serializer = self.get_serializer(events, many=True)
        return Response(serializer.data)
