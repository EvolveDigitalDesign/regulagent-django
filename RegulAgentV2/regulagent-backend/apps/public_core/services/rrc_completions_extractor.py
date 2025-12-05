from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import time
import mimetypes

import requests
from django.conf import settings
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


RRC_COMPLETIONS_SEARCH = (
    "https://webapps.rrc.texas.gov/CMPL/publicSearchAction.do?"
    "formData.methodHndlr.inputValue=init&formData.headerTabSelected=home&formData.pageForwardHndlr.inputValue=home"
)


@dataclass
class DownloadRecord:
    name: str
    url: str
    path: str
    size_bytes: int
    content_type: str


def _media_base() -> Path:
    base = getattr(settings, "MEDIA_ROOT", None)
    return Path(base or ".").resolve() / "rrc" / "completions"


def _ensure_dir(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)


def extract_completions_all_documents(api14: str, allowed_kinds: Optional[List[str]] = None) -> Dict[str, Any]:
    api = re.sub(r"\D+", "", api14)
    if len(api) not in (8, 10, 14):
        raise ValueError("api must be 8, 10, or 14 digits")

    out_dir = _media_base() / api
    _ensure_dir(out_dir)

    # Cache policy: if files exist and the newest is within 14 days, return cached
    now = time.time()
    horizon = 14 * 24 * 60 * 60
    existing_files: List[DownloadRecord] = []
    if out_dir.exists():
        def _infer_kind_from_name(name: str) -> str:
            n = name.lower()
            # Heuristics based on saved filename prefix (e.g., "W-2_", "W-15_")
            # NOTE: GAU is NOT fetched from RRC site - only W-2 and W-15
            if any(k in n for k in ["w-2", "w_2", "w2"]):
                return "w2"
            if any(k in n for k in ["w-15", "w15", "w_15"]):
                return "w15"
            return "other"

        for p in out_dir.glob('*.pdf'):
            try:
                # Skip directional surveys
                if 'directional' in p.name.lower() and 'survey' in p.name.lower():
                    continue
                if allowed_kinds:
                    kind = _infer_kind_from_name(p.stem)
                    if kind not in set(k.lower() for k in allowed_kinds):
                        continue
                mtime = p.stat().st_mtime
                ctype = mimetypes.guess_type(str(p))[0] or 'application/pdf'
                existing_files.append(DownloadRecord(name=p.stem.split('_')[0], url='', path=str(p), size_bytes=p.stat().st_size, content_type=ctype))
            except Exception:
                continue
        if existing_files:
            newest = max(Path(f.path).stat().st_mtime for f in existing_files)
            if (now - newest) <= horizon:
                return {
                    "status": "success",
                    "api": api,
                    "api_search": api[-8:],
                    "output_dir": str(out_dir),
                    "files": [r.__dict__ for r in existing_files],
                    "source": "cache",
                }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(RRC_COMPLETIONS_SEARCH)
            page.wait_for_load_state("networkidle")
            # RRC search expects the 8-digit API root (county+unique); use last 8 digits
            search_api = api[-8:]
            page.fill('input[name="searchArgs.apiNoHndlr.inputValue"]', search_api)
            page.click('input[type="button"][value="Search"][onclick="doSearch();"]')
            page.wait_for_load_state("networkidle")

            # Get all rows from the DataGrid table (not just latest)
            # We need complete well history for proper analysis
            table = page.query_selector("table.DataGrid")
            if not table:
                return {"status": "no_records", "api": api, "api_search": search_api, "files": []}
            rows = table.query_selector_all("tr")[2:]  # skip header/pagination

            def parse_date(cell_text: str) -> tuple:
                # Expect mm/dd/yyyy in one of the columns; scan cells and return sort key
                import datetime as _dt
                for token in re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", cell_text):
                    try:
                        dt = _dt.datetime.strptime(token, "%m/%d/%Y")
                        return (dt.year, dt.month, dt.day)
                    except Exception:
                        continue
                return (0, 0, 0)

            if not rows:
                return {"status": "no_records", "api": api, "api_search": search_api, "files": []}

            # Extract row data BEFORE sorting/navigating (to avoid stale element references)
            # Store as tuples: (date_sort_key, link_href, row_text_for_debug)
            row_data: List[tuple] = []
            for row in rows:
                try:
                    link = row.query_selector("td:first-child a")
                    if not link:
                        continue
                    href = link.get_attribute("href")
                    if not href:
                        continue
                    row_text = row.inner_text() or ""
                    sort_key = parse_date(row_text)
                    row_data.append((sort_key, href, row_text))
                except Exception as e:
                    logger.debug(f"   Failed to extract row data: {e}")
                    continue
            
            if not row_data:
                return {"status": "no_records", "api": api, "api_search": search_api, "files": []}
            
            # Sort by date (chronological order for processing)
            sorted_row_data = sorted(row_data, key=lambda x: x[0])
            
            files: List[DownloadRecord] = []
            seen_hrefs: set[str] = set()
            
            logger.info(f"🔍 Processing {len(sorted_row_data)} rows from RRC search results (in chronological order)")
            
            # Process EACH row in the results (not just the latest)
            for row_idx, (sort_key, href, row_text) in enumerate(sorted_row_data, 1):
                logger.info(f"\n📋 Processing row {row_idx}/{len(sorted_row_data)}")
                logger.debug(f"   Row content: {row_text[:100]}...")

                try:
                    # Navigate to detail page using the href we captured earlier
                    page.goto(f"https://webapps.rrc.texas.gov{href}", wait_until="networkidle")
                    logger.info(f"   ✅ Opened detail page for row {row_idx}")
                except Exception as e:
                    logger.warning(f"   ⚠️  Failed to navigate to detail page for row {row_idx}: {e}")
                    continue

                # Find the Form/Attachment table on this detail page
                documents_table = None
                for tbl in page.query_selector_all("table"):
                    cells = tbl.query_selector_all("th, td")
                    header = " ".join([c.inner_text().strip() for c in cells[:6]])
                    if "Form/Attachment" in header and "View Form/Attachment" in header:
                        documents_table = tbl
                        break
                
                if not documents_table:
                    logger.warning(f"   ⚠️  No Form/Attachment table found in row {row_idx}, skipping")
                    # Go back to search results to process next row
                    try:
                        page.go_back()
                        page.wait_for_load_state("networkidle")
                    except Exception as e:
                        logger.warning(f"   ⚠️  Failed to go back: {e}")
                    continue

                logger.info(f"   📄 Found Form/Attachment table, extracting documents...")
                
                # Extract ALL W-2 and W-15 documents from this detail page (not deduped by type)
                for row in documents_table.query_selector_all("tr"):
                    cols = row.query_selector_all("td, th")
                    if len(cols) < 3:
                        continue
                    form_text = cols[0].inner_text().strip()
                    href = None
                    for a in row.query_selector_all("a"):
                        h = a.get_attribute("href")
                        if h and ("viewPdfReportFormAction.do" in h or "dpimages/r/" in h):
                            href = h
                            break
                    if not href:
                        continue
                    
                    doc_type = form_text.split("\n")[0][:64]
                    # Skip directional survey files per product guidance
                    if "directional survey" in doc_type.lower():
                        logger.debug(f"      Skipping directional survey: {doc_type}")
                        continue
                    
                    # NOTE: We keep ALL instances (W-2 and W-15) - do NOT dedupe by type
                    # Each submission may have multiple versions that we need to track
                    if href in seen_hrefs:
                        logger.debug(f"      Skipping duplicate href: {doc_type}")
                        continue
                    seen_hrefs.add(href)

                    if href.startswith("/"):
                        url = f"https://webapps.rrc.texas.gov{href}"
                    elif href.startswith("http"):
                        url = href
                    else:
                        url = f"https://webapps.rrc.texas.gov/{href}"

                    # Normalize document name from URL when possible (e.g., W-2 PDF endpoint)
                    lower_href = (href or url).lower()
                    # Determine a normalized kind for filtering
                    # NOTE: Only W-2 and W-15 are fetched from RRC - GAU is NOT included
                    kind = "other"
                    if "viewpdfreportformaction.do" in lower_href and "cmplw2formpdf" in lower_href:
                        doc_type = "W-2"  # normalize label
                        kind = "w2"
                    elif "viewpdfreportformaction.do" in lower_href and "cmplw15formpdf" in lower_href:
                        # Heuristic for W-15 endpoint (if present in URL patterns)
                        kind = "w15"
                    else:
                        dt_low = doc_type.lower()
                        if ("w-2" in dt_low) or ("w2" in dt_low):
                            kind = "w2"
                        elif ("w-15" in dt_low) or ("w15" in dt_low) or ("cement" in dt_low):
                            kind = "w15"
                        # Skip GAU documents - they are NOT fetched from RRC site
                        elif any(x in dt_low for x in ["gau", "groundwater", "l-1", "l1"]):
                            logger.debug(f"Skipping GAU document: {doc_type} (not fetched from RRC)")
                            continue

                    # If allowlist provided, skip non-allowed kinds
                    if allowed_kinds and kind not in set(k.lower() for k in allowed_kinds):
                        continue

                    # Create unique filename with timestamp to avoid collisions when downloading multiple versions
                    # Format: W-2_42003001_20250109_120530.pdf or W-15_42003001_20250109_120530.pdf
                    import datetime as _dt
                    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                    safe_type = re.sub(r"[^A-Za-z0-9_.-]", "_", doc_type.replace(" ", "_"))[:32]
                    filename = f"{safe_type}_{api}_{timestamp}.pdf"
                    file_path = out_dir / filename

                    try:
                        logger.debug(f"      Downloading: {doc_type}")
                        resp = requests.get(url, timeout=30)
                        if resp.status_code == 200:
                            with open(file_path, "wb") as f:
                                f.write(resp.content)
                            size = file_path.stat().st_size if file_path.exists() else 0
                            ctype = resp.headers.get("content-type", "")
                            files.append(DownloadRecord(name=doc_type, url=url, path=str(file_path), size_bytes=size, content_type=ctype))
                            logger.info(f"      ✅ Downloaded: {doc_type} ({size:,} bytes)")
                        else:
                            logger.warning(f"      ⚠️  Failed to download (status {resp.status_code}): {doc_type}")
                    except Exception as e:
                        logger.warning(f"      ⚠️  Failed to download {doc_type}: {e}")
                        continue

                # Go back to search results to process next row
                try:
                    logger.info(f"   ↩️  Returning to search results")
                    page.go_back()
                    page.wait_for_load_state("networkidle")
                except Exception as e:
                    logger.warning(f"   ⚠️  Failed to go back to search results: {e}")
                    break

            logger.info(f"\n✅ Completed processing all {len(sorted_rows)} rows")
            logger.info(f"📊 Total files downloaded: {len(files)}")
            
            return {
                "status": "success" if files else "no_documents",
                "api": api,
                "api_search": search_api,
                "output_dir": str(out_dir),
                "files": [r.__dict__ for r in files],
                "source": "rrc_completions",
            }
        finally:
            context.close()
            browser.close()


