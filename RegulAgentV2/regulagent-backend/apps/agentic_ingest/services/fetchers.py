from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Dict, Any, Tuple
from .rrc_config import DEFAULT_RRC_CONFIG

try:
    from playwright.async_api import async_playwright  # type: ignore
except Exception:
    async_playwright = None  # type: ignore


class RRCFetcher:
    def __init__(self, api10: str, workspace: Path) -> None:
        self.api10 = "".join(filter(str.isdigit, api10))
        self.workspace = workspace

    async def fetch(self) -> Tuple[List[Path], List[Dict[str, Any]]]:
        findings: List[Dict[str, Any]] = []
        if async_playwright is None:
            findings.append({
                "code": "AUTOMATION_UNAVAILABLE",
                "severity": "major",
                "message": "Playwright is not available in this environment",
                "context": {}
            })
            return [], findings
        # Prefer new env names; fallback to legacy, then to hardcoded (temporary)
        username = os.getenv("RRC_USERNAME") or os.getenv("RRC_user_id") or "jmropsd1"
        password = os.getenv("RRC_PASSWORD") or os.getenv("RRC_password") or "JMRsvop20"
        # Export to env so downstream automation can read them
        os.environ.setdefault("RRC_USERNAME", username)
        os.environ.setdefault("RRC_PASSWORD", password)
        os.environ.setdefault("RRC_user_id", username)
        os.environ.setdefault("RRC_password", password)
        self.workspace.mkdir(parents=True, exist_ok=True)
        files: List[Path] = []
        async with async_playwright() as p:  # type: ignore
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            try:
                dl, dl_findings = await self._run_rrc_flow(context)
                files.extend(dl)
                findings.extend(dl_findings)
            finally:
                await context.close()
                await browser.close()
        return files, findings

    async def _run_rrc_flow(self, context) -> Tuple[List[Path], List[Dict[str, Any]]]:
        """
        Minimal internal Playwright flow placeholder for RRC completions fetching.
        NOTE: Intentionally avoids importing legacy code. Implement real selectors/URLs here.
        """
        findings: List[Dict[str, Any]] = []
        cfg = DEFAULT_RRC_CONFIG
        page = await context.new_page()
        files: List[Path] = []
        try:
            if cfg.login_url:
                await page.goto(cfg.login_url, wait_until="domcontentloaded")
                if cfg.selectors.username_input:
                    await page.fill(cfg.selectors.username_input, os.getenv("RRC_USERNAME", ""))
                if cfg.selectors.password_input:
                    await page.fill(cfg.selectors.password_input, os.getenv("RRC_PASSWORD", ""))
                if cfg.selectors.login_button:
                    await page.click(cfg.selectors.login_button)
            if cfg.search_url:
                await page.goto(cfg.search_url, wait_until="domcontentloaded")
                api8 = self.api10[-8:]
                if cfg.selectors.api_search_input:
                    await page.fill(cfg.selectors.api_search_input, api8)
                if cfg.selectors.search_button:
                    await page.click(cfg.selectors.search_button)
            # Find completions data table and determine latest record by submit date (7th column)
            table = await page.query_selector('table.DataGrid')
            if table:
                rows = await table.query_selector_all('tr')
                # Heuristic: skip header/pagination rows, iterate data rows
                latest_row_handle = None
                latest_date_val = None
                from datetime import datetime as _dt
                for idx, row in enumerate(rows):
                    # Skip first two rows commonly header/pagination
                    if idx < 2:
                        continue
                    date_cell = await row.query_selector('td:nth-child(7)')
                    if not date_cell:
                        continue
                    txt = (await date_cell.text_content()) or ""
                    txt = txt.strip()
                    if not txt:
                        continue
                    try:
                        parsed = _dt.strptime(txt, '%m/%d/%Y')
                        if latest_date_val is None or parsed > latest_date_val:
                            latest_date_val = parsed
                            latest_row_handle = row
                    except Exception:
                        continue
                # Click tracking link in first column of the latest row
                if latest_row_handle is not None:
                    first_cell_link = await latest_row_handle.query_selector('td:first-child a')
                    if first_cell_link:
                        await first_cell_link.click()
                        await page.wait_for_load_state('networkidle')
            # On the detail page, scan for all document links in any table rows
            download_dir = self.workspace
            doc_links = await page.query_selector_all("a[href*='viewPdfReportFormAction.do'], a[href*='dpimages/r/']")
            for ln in doc_links:
                href = await ln.get_attribute('href')
                if not href:
                    continue
                try:
                    with page.expect_download() as dl_info:
                        await ln.click()
                    dl = await dl_info.value
                    name = await dl.suggested_filename()
                    dest = download_dir / (name or f'doc_{len(files)+1}.pdf')
                    await dl.save_as(dest)
                    files.append(dest)
                except Exception:
                    # Some links may open in viewer; skip silently for now
                    continue
            if not files:
                findings.append({
                    "code": "RRC_NO_DOCUMENTS_FOUND",
                    "severity": "minor",
                    "message": "No documents were discovered for the API on the RRC pages with current selectors.",
                    "context": {"api10": self.api10}
                })
        except Exception as e:
            findings.append({
                "code": "RRC_FLOW_ERROR",
                "severity": "major",
                "message": str(e),
                "context": {"api10": self.api10}
            })
        finally:
            await page.close()
        return files, findings


