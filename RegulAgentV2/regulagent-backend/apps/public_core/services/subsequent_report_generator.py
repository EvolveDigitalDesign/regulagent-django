"""Subsequent Report Generator for NM C-103P.

Takes a filed C-103 NOI and uploaded DWR data to produce:
1. Day-by-day narrative from DWRs
2. As-plugged WBD data (actual plug placements)
3. Plug reconciliation (planned vs actual)
4. Complete subsequent report form data
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import date

logger = logging.getLogger(__name__)

# Plug-placement event types — same set as plug_reconciliation.py
_PLUG_PLACEMENT_TYPES = {
    "set_cement_plug",
    "set_surface_plug",
    "set_bridge_plug",
    "set_marker",
    "squeeze",
}


@dataclass
class SubsequentReportData:
    """Complete data for a C-103P subsequent report."""
    api_number: str
    noi_form_id: int

    # Day-by-day operations
    daily_summaries: List[dict] = field(default_factory=list)
    # Each: {day_number, work_date, narrative, events: [...]}

    # As-plugged data
    actual_plugs: List[dict] = field(default_factory=list)
    # Each: {plug_number, type, top_ft, bottom_ft, sacks, cement_class, tagged_depth}

    # Reconciliation
    reconciliation: Optional[dict] = None  # From PlugReconciliationEngine

    # Report narrative
    operations_narrative: str = ""
    total_days: int = 0
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class SubsequentReportGenerator:
    """Generate subsequent reports from NOI + DWR data."""

    def __init__(self):
        from .dwr_parser import DWRParser
        from .plug_reconciliation import PlugReconciliationEngine
        self.dwr_parser = DWRParser()
        self.reconciliation_engine = PlugReconciliationEngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_from_dwrs(self, noi_form, dwr_parse_results) -> SubsequentReportData:
        """Generate subsequent report from a filed NOI and parsed DWR data.

        Args:
            noi_form: C103FormORM instance (the original NOI)
            dwr_parse_results: DWRParseResult from DWR parser

        Returns:
            SubsequentReportData with all report components
        """
        logger.info(
            "SubsequentReportGenerator.generate_from_dwrs: noi_form_id=%s api=%s days=%d",
            noi_form.id,
            noi_form.api_number,
            dwr_parse_results.total_days,
        )

        report = SubsequentReportData(
            api_number=noi_form.api_number,
            noi_form_id=noi_form.id,
        )

        report.daily_summaries = self._build_daily_summaries(dwr_parse_results)
        report.actual_plugs = self._extract_actual_plugs(dwr_parse_results)
        report.reconciliation = self._run_reconciliation(noi_form, report.actual_plugs)
        report.operations_narrative = self._generate_operations_narrative(report.daily_summaries)
        report.total_days = dwr_parse_results.total_days

        if dwr_parse_results.days:
            sorted_dates = sorted(d.work_date for d in dwr_parse_results.days)
            report.start_date = sorted_dates[0]
            report.end_date = sorted_dates[-1]

        return report

    def generate_from_uploaded_pdfs(self, noi_form, pdf_paths: list) -> SubsequentReportData:
        """Parse DWR PDFs and generate subsequent report in one call."""
        logger.info(
            "SubsequentReportGenerator.generate_from_uploaded_pdfs: noi_form_id=%s pdfs=%d",
            noi_form.id,
            len(pdf_paths),
        )
        dwr_result = self.dwr_parser.parse_multiple(pdf_paths, api_number=noi_form.api_number)
        return self.generate_from_dwrs(noi_form, dwr_result)

    # ------------------------------------------------------------------
    # Report Components
    # ------------------------------------------------------------------

    def _build_daily_summaries(self, dwr_result) -> list:
        """Build day-by-day operation summaries from DWR data."""
        summaries = []
        for day in dwr_result.days:
            events_data = []
            for ev in day.events:
                events_data.append({
                    "event_type": ev.event_type,
                    "description": ev.description,
                    "start_time": ev.start_time.isoformat() if ev.start_time else None,
                    "end_time": ev.end_time.isoformat() if ev.end_time else None,
                    "depth_top_ft": ev.depth_top_ft,
                    "depth_bottom_ft": ev.depth_bottom_ft,
                    "tagged_depth_ft": ev.tagged_depth_ft,
                    "cement_class": ev.cement_class,
                    "sacks": ev.sacks,
                    "volume_bbl": ev.volume_bbl,
                    "pressure_psi": ev.pressure_psi,
                    "plug_number": ev.plug_number,
                    "casing_string": ev.casing_string,
                })

            summaries.append({
                "day_number": day.day_number,
                "work_date": day.work_date.isoformat(),
                "narrative": day.daily_narrative,
                "rig_name": day.rig_name,
                "weather": day.weather,
                "crew_size": day.crew_size,
                "events": events_data,
            })

        return summaries

    def _extract_actual_plugs(self, dwr_result) -> list:
        """Extract actual plug placements from DWR events.

        Filters for plug-placement events and builds a numbered plug list.
        """
        plug_events = []
        for day in dwr_result.days:
            for ev in day.events:
                if ev.event_type in _PLUG_PLACEMENT_TYPES:
                    plug_events.append({
                        "event_type": ev.event_type,
                        "work_date": day.work_date.isoformat(),
                        "depth_top_ft": ev.depth_top_ft,
                        "depth_bottom_ft": ev.depth_bottom_ft,
                        "tagged_depth_ft": ev.tagged_depth_ft,
                        "sacks": ev.sacks,
                        "cement_class": ev.cement_class,
                        "plug_number": ev.plug_number,
                        "description": ev.description,
                        "casing_string": ev.casing_string,
                    })

        # Sort deepest first (mirrors reconciliation engine convention)
        def _midpoint(ev):
            top = ev.get("depth_top_ft")
            bottom = ev.get("depth_bottom_ft")
            if top is not None and bottom is not None:
                return (top + bottom) / 2.0
            return top if top is not None else (bottom if bottom is not None else 0.0)

        plug_events.sort(key=_midpoint, reverse=True)

        # Assign sequential plug numbers where not already set
        actual_plugs = []
        for idx, ev in enumerate(plug_events, start=1):
            actual_plugs.append({
                "plug_number": ev.get("plug_number") or idx,
                "type": ev["event_type"],
                "top_ft": ev["depth_top_ft"],
                "bottom_ft": ev["depth_bottom_ft"],
                "sacks": ev["sacks"],
                "cement_class": ev["cement_class"],
                "tagged_depth": ev["tagged_depth_ft"],
                "work_date": ev["work_date"],
                "description": ev["description"],
                "casing_string": ev["casing_string"],
                # Fields expected by PlugReconciliationEngine._get_actual_midpoint
                "depth_top_ft": ev["depth_top_ft"],
                "depth_bottom_ft": ev["depth_bottom_ft"],
                "tagged_depth_ft": ev["tagged_depth_ft"],
            })

        return actual_plugs

    def _run_reconciliation(self, noi_form, actual_plugs) -> dict:
        """Run plug reconciliation between planned (NOI) and actual (DWR).

        Gets planned plugs from noi_form.plugs.all() and compares.
        Returns a dict representation of ReconciliationResult.
        """
        planned_plugs = []
        for plug in noi_form.plugs.all():
            planned_plugs.append({
                "plug_number": plug.plug_number,
                "step_type": plug.step_type,
                "top_ft": plug.top_ft,
                "bottom_ft": plug.bottom_ft,
                "sacks_required": plug.sacks_required,
                "cement_class": plug.cement_class or None,
                "formation_name": plug.formation_name or None,
            })

        result = self.reconciliation_engine.reconcile(
            planned_plugs=planned_plugs,
            actual_events=actual_plugs,
            api_number=noi_form.api_number,
            c103_form_id=noi_form.id,
        )

        comparisons = []
        for comp in result.comparisons:
            comparisons.append({
                "plug_number": comp.plug_number,
                "planned_type": comp.planned_type,
                "planned_top_ft": comp.planned_top_ft,
                "planned_bottom_ft": comp.planned_bottom_ft,
                "planned_sacks": comp.planned_sacks,
                "planned_cement_class": comp.planned_cement_class,
                "planned_formation": comp.planned_formation,
                "actual_type": comp.actual_type,
                "actual_top_ft": comp.actual_top_ft,
                "actual_bottom_ft": comp.actual_bottom_ft,
                "actual_sacks": comp.actual_sacks,
                "actual_cement_class": comp.actual_cement_class,
                "actual_tagged_depth_ft": comp.actual_tagged_depth_ft,
                "deviation_level": comp.deviation_level.value,
                "depth_deviation_ft": comp.depth_deviation_ft,
                "sack_deviation_pct": comp.sack_deviation_pct,
                "deviation_notes": comp.deviation_notes,
            })

        return {
            "api_number": result.api_number,
            "c103_form_id": result.c103_form_id,
            "total_planned": result.total_planned,
            "total_actual": result.total_actual,
            "matches": result.matches,
            "minor_deviations": result.minor_deviations,
            "major_deviations": result.major_deviations,
            "added_plugs": result.added_plugs,
            "missing_plugs": result.missing_plugs,
            "overall_status": result.overall_status,
            "summary_narrative": result.summary_narrative,
            "comparisons": comparisons,
        }

    def _generate_operations_narrative(self, daily_summaries) -> str:
        """Generate complete operations narrative from daily summaries.

        Prose format suitable for C-103P attachment.
        """
        if not daily_summaries:
            return ""

        paragraphs = []
        for summary in daily_summaries:
            day_num = summary["day_number"]
            work_date = summary["work_date"]
            narrative = summary.get("narrative", "").strip()
            events = summary.get("events", [])

            # Build header line
            header = f"Day {day_num} ({work_date}):"

            if narrative:
                paragraphs.append(f"{header} {narrative}")
            elif events:
                # Build narrative from events if daily_narrative is empty
                event_lines = []
                for ev in events:
                    parts = [ev.get("description") or ev["event_type"].replace("_", " ").title()]
                    if ev.get("depth_top_ft") is not None:
                        parts.append(f"@ {ev['depth_top_ft']:,.0f} ft")
                    if ev.get("tagged_depth_ft") is not None:
                        parts.append(f"(tagged {ev['tagged_depth_ft']:,.0f} ft)")
                    if ev.get("sacks") is not None:
                        parts.append(f"{ev['sacks']:g} sks")
                    if ev.get("cement_class"):
                        parts.append(f"Class {ev['cement_class']}")
                    event_lines.append(" ".join(parts))
                paragraphs.append(f"{header} {'; '.join(event_lines)}.")
            else:
                paragraphs.append(f"{header} No operations recorded.")

        return "\n\n".join(paragraphs)

    # ------------------------------------------------------------------
    # ORM Persistence
    # ------------------------------------------------------------------

    def create_subsequent_form(self, noi_form, report_data: SubsequentReportData):
        """Create a new C103FormORM with form_type='subsequent' from report data.

        Links back to the original NOI via plan_data['noi_form_id'].
        Also creates DailyWorkRecord and C103EventORM entries.

        Returns:
            The new C103FormORM instance.
        """
        from apps.public_core.models.c103_orm import C103FormORM, DailyWorkRecord, C103EventORM

        logger.info(
            "SubsequentReportGenerator.create_subsequent_form: noi_form_id=%s api=%s",
            noi_form.id,
            noi_form.api_number,
        )

        # Build plan_data JSON blob for the subsequent form
        plan_data = {
            "noi_form_id": noi_form.id,
            "actual_plugs": report_data.actual_plugs,
            "reconciliation": report_data.reconciliation,
            "total_days": report_data.total_days,
            "start_date": report_data.start_date.isoformat() if report_data.start_date else None,
            "end_date": report_data.end_date.isoformat() if report_data.end_date else None,
        }

        subsequent_form = C103FormORM.objects.create(
            well=noi_form.well,
            api_number=noi_form.api_number,
            form_type="subsequent",
            status="draft",
            tenant_id=noi_form.tenant_id,
            workspace=noi_form.workspace,
            region=noi_form.region,
            sub_area=noi_form.sub_area,
            coa_figure=noi_form.coa_figure,
            lease_type=noi_form.lease_type,
            plan_snapshot=noi_form.plan_snapshot,
            proposed_work_narrative=report_data.operations_narrative,
            plan_data=plan_data,
        )

        # Create DailyWorkRecord + C103EventORM entries for each day
        for summary in report_data.daily_summaries:
            from datetime import date as _date

            work_date = _date.fromisoformat(summary["work_date"])

            dwr_record = DailyWorkRecord.objects.create(
                c103_form=subsequent_form,
                work_date=work_date,
                day_number=summary["day_number"],
                daily_narrative=summary.get("narrative", ""),
            )

            # Create events and link to the daily record
            for ev_data in summary.get("events", []):
                start_time = None
                end_time = None
                if ev_data.get("start_time"):
                    from datetime import time as _time
                    try:
                        h, m = ev_data["start_time"].split(":")[:2]
                        start_time = _time(int(h), int(m))
                    except (ValueError, AttributeError):
                        pass
                if ev_data.get("end_time"):
                    from datetime import time as _time
                    try:
                        h, m = ev_data["end_time"].split(":")[:2]
                        end_time = _time(int(h), int(m))
                    except (ValueError, AttributeError):
                        pass

                event = C103EventORM.objects.create(
                    well=noi_form.well,
                    c103_form=subsequent_form,
                    api_number=noi_form.api_number,
                    event_type=ev_data["event_type"],
                    event_date=work_date,
                    event_start_time=start_time,
                    event_end_time=end_time,
                    depth_top_ft=ev_data.get("depth_top_ft"),
                    depth_bottom_ft=ev_data.get("depth_bottom_ft"),
                    tagged_depth_ft=ev_data.get("tagged_depth_ft"),
                    cement_class=ev_data.get("cement_class") or "",
                    sacks=ev_data.get("sacks"),
                    volume_bbl=ev_data.get("volume_bbl"),
                    pressure_psi=ev_data.get("pressure_psi"),
                    plug_number=ev_data.get("plug_number"),
                    casing_string=ev_data.get("casing_string") or "",
                    raw_event_detail=ev_data.get("description", ""),
                )
                dwr_record.events.add(event)

        logger.info(
            "SubsequentReportGenerator.create_subsequent_form: created id=%s",
            subsequent_form.id,
        )
        return subsequent_form
