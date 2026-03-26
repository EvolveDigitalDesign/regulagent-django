"""
Neubus Ingestion Pipeline.

Simple flow:
1. Navigate to Neubus search URL with API8 number
2. Count rows in search results table
3. For each row:
   a. Click the row
   b. Find every "Actions" button (one per tab section)
   c. Click Actions → Download Tab for each
   d. Wait for downloads to settle
   e. Click "Search Results" to go back
4. Save files to cold storage
5. Update DB records
"""
from __future__ import annotations

import logging
import os
import shutil
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.db import transaction

from apps.public_core.models.neubus_lease import NeubusLease, NeubusDocument
from apps.public_core.services.neubus_client import NeubusClient, NeubusAuthError, NeubusSearchError
from apps.public_core.services.neubus_storage import ColdStorageManager

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Primary entry point
# ──────────────────────────────────────────────────────────────

def ingest_lease(api_number: str) -> NeubusLease:
    """
    Download all Neubus documents for a well.

    Navigates to Neubus search by API, clicks each row,
    downloads all tabs, saves files to cold storage.
    """
    clean = api_number.replace("-", "").replace(" ", "")
    if len(clean) >= 10:
        # API14 (14 digits) or API10 (10 digits): has state prefix → skip first 2
        api8 = clean[2:10]
    elif len(clean) >= 8:
        # API8 or API9: already county(3) + unique(5), no state prefix
        api8 = clean[:8]
    else:
        # Shorter than 8 digits: pad right with zeros to 8
        api8 = clean.ljust(8, "0")

    with NeubusClient() as client:
        # Step 1: Navigate to search results
        url = (
            f"{client.NEUBUS_BASE}/search-profile"
            f"?profileId={client.PROFILE_ID}&search_fields-api_ft={api8}"
        )
        logger.info(f"[Neubus Ingest] Navigating to {url}")
        client._page.goto(url, wait_until="networkidle", timeout=30000)
        client._page.wait_for_timeout(4000)

        # Step 2: Get row count
        rows = client._page.query_selector_all("table tbody tr")
        total_rows = len(rows)
        if total_rows == 0:
            raise NeubusSearchError(
                f"No Neubus records found for API {api_number} (api8={api8})"
            )

        logger.info(f"[Neubus Ingest] Found {total_rows} result row(s)")

        # Extract lease metadata from the first page's Vuex store.
        # _extract_lease_metadata scans all rows to find whichever one has a
        # lease_number — the same lease number applies to all rows/tabs on the page.
        lease_meta = _extract_lease_metadata(client._page)
        lease_id = lease_meta.get("lease_number") or api8
        if not lease_meta.get("lease_number"):
            logger.warning(
                f"[Neubus Ingest] No lease_number found in any search result row "
                f"for api8={api8}; falling back to api8 as lease_id"
            )

        storage = ColdStorageManager(lease_id)
        all_downloads: List[Dict[str, Any]] = []

        # Step 3: Process each row
        for i in range(total_rows):
            # Re-query rows each iteration (Vue re-renders after back-navigation)
            rows = client._page.query_selector_all("table tbody tr")
            if i >= len(rows):
                logger.warning(f"[Neubus Ingest] Row {i} no longer exists, stopping")
                break

            logger.info(f"[Neubus Ingest] Row {i + 1} / {total_rows}")
            _dismiss_swal(client._page)
            client._page.evaluate(
                "(el) => { el.scrollIntoView({block: 'center'}); el.click(); }",
                rows[i],
            )

            # Wait for record detail panel to load
            try:
                client._page.wait_for_selector(
                    'button:has-text("Actions")', timeout=10000
                )
            except Exception:
                logger.warning(
                    f"[Neubus Ingest] Row {i}: no Actions button found, skipping"
                )
                _go_back(client._page)
                continue
            client._page.wait_for_timeout(500)

            # Step 4: Download all tabs
            row_downloads = _download_all_tabs(client._page, storage, i)
            all_downloads.extend(row_downloads)
            logger.info(f"[Neubus Ingest] Row {i + 1}: {len(row_downloads)} files downloaded")

            # Step 5: Go back to search results
            _go_back(client._page)
            logger.info(f"[Neubus Ingest] Back to search results after row {i + 1}")

        logger.info(
            f"[Neubus Ingest] Complete: {len(all_downloads)} file(s) downloaded "
            f"across {total_rows} row(s)"
        )

    # Step 6: Create/update DB records (outside Playwright context to avoid
    # Django SynchronousOnlyOperation from Playwright's event loop)
    return _update_db_records(
        lease_id=lease_id,
        lease_meta=lease_meta,
        downloads=all_downloads,
        storage=storage,
    )


def ingest_lease_if_stale(api_number: str, max_age_hours: int = 24) -> Optional[NeubusLease]:
    """
    Return cached lease if it exists, regardless of age.
    Only fetches from Neubus if no lease data exists at all.

    The max_age_hours parameter is kept for backward compatibility but ignored.
    Users can force a re-fetch via the resync endpoint (force_fetch=True on POST /api/research/sessions/).
    """
    from apps.public_core.models import WellRegistry

    clean_api = api_number.replace("-", "").replace(" ", "")

    # Strategy 1: Check via WellRegistry.lease_id
    well = WellRegistry.objects.filter(api14__icontains=clean_api[-8:]).first()
    if well and well.lease_id:
        existing = NeubusLease.objects.filter(lease_id=well.lease_id).first()
        if existing:
            logger.info(
                f"[Neubus Ingest] Lease {existing.lease_id} found in cache (via WellRegistry), "
                f"last_checked={existing.last_checked}. Skipping re-fetch."
            )
            return existing

    # Strategy 2: Check via NeubusDocument.api (triage may have set this)
    from apps.public_core.models.neubus_lease import NeubusDocument
    doc_match = NeubusDocument.objects.filter(api__icontains=clean_api[-8:]).first()
    if doc_match and doc_match.lease:
        logger.info(
            f"[Neubus Ingest] Lease {doc_match.lease.lease_id} found in cache (via NeubusDocument), "
            f"last_checked={doc_match.lease.last_checked}. Skipping re-fetch."
        )
        return doc_match.lease

    # No cached lease — fetch fresh
    logger.info(f"[Neubus Ingest] No cached lease for {api_number}, fetching from Neubus")
    return ingest_lease(api_number)


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────

def _dismiss_swal(page) -> None:
    """Dismiss any SweetAlert modal that may be blocking the page."""
    try:
        swal = page.query_selector(".swal2-container")
        if swal:
            # Try clicking the confirm/OK button first
            confirm = page.query_selector(".swal2-confirm")
            if confirm:
                page.evaluate("(el) => el.click()", confirm)
                page.wait_for_timeout(300)
                return
            # Try clicking the close button
            close = page.query_selector(".swal2-close")
            if close:
                page.evaluate("(el) => el.click()", close)
                page.wait_for_timeout(300)
                return
            # Last resort: remove the overlay via JS
            page.evaluate("""
                () => {
                    var swal = document.querySelector('.swal2-container');
                    if (swal) swal.remove();
                }
            """)
            page.wait_for_timeout(300)
    except Exception:
        pass


def _extract_lease_metadata(page) -> dict:
    """Extract lease metadata from the Vuex store on the current search results page.

    Scans ALL search result rows and returns the one with the most complete
    metadata (i.e., has a lease_number). Some rows (e.g., SWR-10 filings)
    have empty lease info while sibling rows on the same search have full data.
    The lease number on the page applies to all wells/tabs in the search results.
    """
    try:
        result = page.evaluate("""
        () => {
            var app = document.querySelector('#app');
            if (!app) return {};
            var store = app.__vue_app__
                ? app.__vue_app__.config.globalProperties.$store
                : (app.__vue__ ? app.__vue__.$store : null);
            if (!store) return {};
            var sr = store.state.searchResult;
            var images = sr && sr.search_results ? sr.search_results.images || [] : [];
            if (images.length === 0) return {};

            // Scan all rows, prefer the one with a lease_number
            var best = null;
            for (var i = 0; i < images.length; i++) {
                var fields = {};
                (images[i].image_fields || []).forEach(function(f) {
                    fields[f.field_name] = f.field_value;
                });
                if (fields.lease_number) {
                    return fields;  // Found one with lease_number, use it
                }
                if (!best) best = fields;  // Keep first as fallback
            }
            return best || {};
        }
        """)
        return result or {}
    except Exception:
        return {}


def _download_all_tabs(page, storage: ColdStorageManager, row_index: int) -> List[Dict[str, Any]]:
    """
    For the current record detail page, discover ALL sub-tab containers via JS,
    then click Actions → Download Tab for each container that has files.

    Uses JavaScript to enumerate `.nde-view-record-item .pl-10.pr-6` containers
    so that initially hidden tabs (Document, Well Log, etc.) are also found.

    Returns list of download info dicts with name, path, and hash.
    """
    downloads: List[Dict[str, Any]] = []

    # Step 1: Discover all sub-tab containers via JS
    tab_info = page.evaluate("""
    () => {
        var containers = document.querySelectorAll('.nde-view-record-item .pl-10.pr-6');
        var tabs = [];
        for (var i = 0; i < containers.length; i++) {
            var c = containers[i];
            var text = c.innerText || '';
            var hasNoFiles = text.includes('No Files Uploaded');
            var labelBtn = c.querySelector('button[aria-label]');
            var label = labelBtn ? (labelBtn.getAttribute('aria-label') || '') : '';
            var actionBtn = null;
            var btns = c.querySelectorAll('button');
            for (var j = 0; j < btns.length; j++) {
                if ((btns[j].innerText || '').trim().toLowerCase().startsWith('actions')) {
                    actionBtn = btns[j];
                    break;
                }
            }
            tabs.push({
                idx: i,
                label: label,
                hasNoFiles: hasNoFiles,
                hasActionBtn: !!actionBtn
            });
        }
        return tabs;
    }
    """)

    logger.info(
        f"  Row {row_index + 1}: found {len(tab_info)} sub-tab container(s): "
        f"{[(t['label'], 'has_files' if not t['hasNoFiles'] else 'empty') for t in tab_info]}"
    )

    # Step 2: Download each tab that has files and an Actions button
    downloadable_tabs = [t for t in tab_info if not t['hasNoFiles'] and t['hasActionBtn']]

    for t_idx, tab in enumerate(downloadable_tabs):
        tab_label = tab['label'] or f"Tab {tab['idx']}"
        container_idx = tab['idx']
        tab_downloads: List[Dict[str, Any]] = []

        def on_download(download, _row=row_index, _tab=t_idx, _acc=tab_downloads):
            name = download.suggested_filename
            path = f"/tmp/neubus_dl_{_row}_{_tab}_{name}"
            download.save_as(path)
            size = os.path.getsize(path)
            _acc.append({"name": name, "path": path, "size": size})
            logger.info(f"    Download: {name} ({size:,} bytes)")

        page.on("download", on_download)

        try:
            _dismiss_swal(page)

            # Click the Actions button inside this specific container via JS
            # (avoids Playwright visibility checks that fail after DOM re-renders)
            clicked = page.evaluate("""
            (containerIdx) => {
                var containers = document.querySelectorAll('.nde-view-record-item .pl-10.pr-6');
                if (containerIdx >= containers.length) return false;
                var c = containers[containerIdx];
                var btns = c.querySelectorAll('button');
                for (var j = 0; j < btns.length; j++) {
                    if ((btns[j].innerText || '').trim().toLowerCase().startsWith('actions')) {
                        btns[j].scrollIntoView({block: 'center'});
                        btns[j].click();
                        return true;
                    }
                }
                return false;
            }
            """, container_idx)

            if not clicked:
                logger.warning(f"    {tab_label}: could not click Actions button")
                continue

            page.wait_for_timeout(500)

            # Click "Download Tab" menu item
            dl_item = page.query_selector(".v-list-item:has-text('Download Tab')")
            if not dl_item:
                dl_item = page.query_selector("text=Download Tab")

            if dl_item:
                page.evaluate("(el) => el.click()", dl_item)

                # Wait for downloads to complete. The FS server POST
                # (bulk/archive) can take 10+ seconds for large records.
                # Strategy: wait up to 15s for the first download to start,
                # then 3s of no new downloads = done.
                # IMPORTANT: use page.wait_for_timeout, NOT time.sleep.
                last_count = 0
                stable = 0
                waited = 0
                while True:
                    page.wait_for_timeout(1000)
                    waited += 1
                    if len(tab_downloads) == last_count:
                        stable += 1
                    else:
                        last_count = len(tab_downloads)
                        stable = 0
                    # After first download arrives, wait 3s of quiet
                    if last_count > 0 and stable >= 3:
                        break
                    # If no downloads after max_initial_wait, give up
                    if last_count == 0 and waited >= 15:
                        break

                logger.info(f"    {tab_label}: {len(tab_downloads)} file(s)")
            else:
                logger.warning(f"    {tab_label}: no 'Download Tab' menu item found")
                page.keyboard.press("Escape")

        except Exception as exc:
            logger.warning(f"    {tab_label} download failed: {exc}")
        finally:
            page.remove_listener("download", on_download)

        # Process each downloaded file — extract zips, save to cold storage
        for dl in tab_downloads:
            dl_path = Path(dl["path"])
            try:
                if zipfile.is_zipfile(dl_path):
                    with zipfile.ZipFile(dl_path) as zf:
                        for name_in_zip in zf.namelist():
                            if name_in_zip.endswith("/"):
                                continue
                            dest = storage.file_path(name_in_zip)
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(name_in_zip) as src, open(dest, "wb") as dst:
                                dst.write(src.read())
                            file_hash = ColdStorageManager.compute_hash(dest)
                            storage.register_file(
                                filename=name_in_zip,
                                size_bytes=dest.stat().st_size,
                                file_hash=file_hash,
                                metadata={"row": row_index, "tab": t_idx, "tab_label": tab_label},
                            )
                            downloads.append({
                                "name": name_in_zip,
                                "path": str(dest),
                                "hash": file_hash,
                            })
                else:
                    # Not a zip — save directly
                    dest = storage.file_path(dl["name"])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dl_path), str(dest))
                    file_hash = ColdStorageManager.compute_hash(dest)
                    storage.register_file(
                        filename=dl["name"],
                        size_bytes=dest.stat().st_size,
                        file_hash=file_hash,
                        metadata={"row": row_index, "tab": t_idx, "tab_label": tab_label},
                    )
                    downloads.append({
                        "name": dl["name"],
                        "path": str(dest),
                        "hash": file_hash,
                    })
            except Exception as exc:
                logger.error(f"    Failed to process download {dl['name']}: {exc}")
            finally:
                try:
                    dl_path.unlink(missing_ok=True)
                except Exception:
                    pass

    return downloads


def _go_back(page) -> None:
    """Navigate back to search results."""
    try:
        _dismiss_swal(page)
        back_link = page.query_selector("text=Search Results")
        if back_link:
            back_link.click()
            page.wait_for_selector("table tbody tr", timeout=10000)
            page.wait_for_timeout(500)
        else:
            page.go_back()
            page.wait_for_timeout(2000)
    except Exception as exc:
        logger.warning(f"[Neubus Ingest] Go back failed: {exc}")


def _update_db_records(
    lease_id: str,
    lease_meta: dict,
    downloads: List[Dict[str, Any]],
    storage: ColdStorageManager,
) -> NeubusLease:
    """Create or update NeubusLease and NeubusDocument records from downloads."""
    with transaction.atomic():
        lease, created = NeubusLease.objects.update_or_create(
            lease_id=lease_id,
            defaults={
                "field_name": lease_meta.get("field_name", ""),
                "lease_name": lease_meta.get("lease_name", ""),
                "operator": lease_meta.get("operator_name", ""),
                "county": lease_meta.get("county", ""),
                "district": lease_meta.get("district", ""),
                "last_checked": date.today(),
            },
        )

        action = "Created" if created else "Updated"
        logger.info(f"[Neubus Ingest] {action} NeubusLease: {lease}")

        for dl in downloads:
            name = dl["name"]
            well_number = name.split("_")[0] if name and "_" in name else ""
            doc, created = NeubusDocument.objects.get_or_create(
                neubus_filename=name,
                defaults={
                    "lease": lease,
                    "file_hash": dl.get("hash", ""),
                    "local_path": dl.get("path", ""),
                    "well_number": well_number,
                    "classification_status": "pending",
                    "extraction_status": "pending",
                },
            )
            if not created:
                # Always update lease and path
                doc.lease = lease
                doc.local_path = dl.get("path", "")
                # Only reset status if file content changed
                new_hash = dl.get("hash", "")
                if new_hash and new_hash != doc.file_hash:
                    doc.file_hash = new_hash
                    doc.classification_status = "pending"
                    doc.extraction_status = "pending"
                doc.save()

    return lease
