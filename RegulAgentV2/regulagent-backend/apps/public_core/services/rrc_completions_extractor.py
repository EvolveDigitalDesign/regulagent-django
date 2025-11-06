from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import time
import mimetypes

import requests
from django.conf import settings
from playwright.sync_api import sync_playwright


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
            # Heuristics based on saved filename prefix (e.g., "W-2_", "W-15_", "L-1_", "GAU_LETTER_")
            if any(k in n for k in ["w-2", "w_2", "w2"]):
                return "w2"
            if any(k in n for k in ["w-15", "w15", "w_15"]):
                return "w15"
            if any(k in n for k in ["gau", "groundwater", "l-1", "l1"]):
                return "gau"
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

            # Find the latest row by submit date (table.DataGrid)
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

            best_row = None
            best_key = (0, 0, 0)
            for r in rows:
                txt = (r.inner_text() or "")
                key = parse_date(txt)
                if key > best_key:
                    best_key = key
                    best_row = r

            if not best_row:
                return {"status": "no_records", "api14": api, "files": []}

            link = best_row.query_selector("td:first-child a")
            if not link:
                return {"status": "error", "error": "tracking_link_not_found", "api": api, "api_search": search_api, "files": []}

            link.click()
            page.wait_for_load_state("networkidle")

            # Find the Form/Attachment table
            documents_table = None
            for tbl in page.query_selector_all("table"):
                cells = tbl.query_selector_all("th, td")
                header = " ".join([c.inner_text().strip() for c in cells[:6]])
                if "Form/Attachment" in header and "View Form/Attachment" in header:
                    documents_table = tbl
                    break
            if not documents_table:
                return {"status": "no_documents", "api": api, "api_search": search_api, "files": []}

            files: List[DownloadRecord] = []
            seen_types: set[str] = set()
            seen_hrefs: set[str] = set()
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
                # dedupe by type and href
                doc_type = form_text.split("\n")[0][:64]
                # Skip directional survey files per product guidance
                if "directional survey" in doc_type.lower():
                    continue
                if href in seen_hrefs or doc_type in seen_types:
                    continue
                seen_hrefs.add(href)
                seen_types.add(doc_type)

                if href.startswith("/"):
                    url = f"https://webapps.rrc.texas.gov{href}"
                elif href.startswith("http"):
                    url = href
                else:
                    url = f"https://webapps.rrc.texas.gov/{href}"

                # Normalize document name from URL when possible (e.g., W-2 PDF endpoint)
                lower_href = (href or url).lower()
                # Determine a normalized kind for filtering
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
                    elif any(x in dt_low for x in ["gau", "groundwater", "l-1", "l1"]):
                        kind = "gau"

                # If allowlist provided, skip non-allowed kinds
                if allowed_kinds and kind not in set(k.lower() for k in allowed_kinds):
                    continue

                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", doc_type.replace(" ", "_"))[:64] or "document"
                filename = f"{safe_name}_{api}.pdf"
                file_path = out_dir / filename

                try:
                    resp = requests.get(url, timeout=30)
                    if resp.status_code == 200:
                        with open(file_path, "wb") as f:
                            f.write(resp.content)
                        size = file_path.stat().st_size if file_path.exists() else 0
                        ctype = resp.headers.get("content-type", "")
                        files.append(DownloadRecord(name=doc_type, url=url, path=str(file_path), size_bytes=size, content_type=ctype))
                except Exception:
                    continue

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


