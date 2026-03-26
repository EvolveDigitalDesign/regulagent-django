"""
Neubus RRC Document Search API Client.

Neubus hosts the TX RRC well records archive at rrcsearch3.neubus.com.
The site is a Vue.js SPA authenticated via Keycloak with a self-signed public JWT.
Profile 17 = Oil and Gas Well Records.

Key Discovery: Records are indexed by lease_number, NOT by API number.
The api_number field is often empty, but the api_ft (full-text) search field
works reliably to find records by API. Prefer search_by_api() when starting
from an API number; use search_by_lease() when the lease number is known.

The Keycloak token cannot be replicated in pure Python — the server responds
differently to API calls that lack the full browser session state. Therefore,
ALL operations (search, file listing, download) are performed through a
persistent Playwright browser session.

Architecture — file listing and download:
    The Neubus NDE is a Vue 2 + Inertia.js SPA. Clicking a search result row
    expands a `nde-view-record` panel that loads file data into Vue component
    instances named `nde-view-record-item-table`. Each instance holds:
        $data.fileData   — {tabname, total_files, box_type, image_id, files: [...]}
        $data.filesList  — same file array
        $props.tabName   — tab label ("Main", "Document", "Well Log", …)
        $props.doc_id    — document identifier

    The FS server (rrcsearch3fs.neubus.com) blocks direct Python requests (WAF).
    Downloads MUST go through the browser via Playwright's expect_download().

Usage:
    with NeubusClient() as client:
        records = client.search_by_lease("15874")
        for i, record in enumerate(records):
            files = client.get_record_files(i)
            if files:
                zip_path, size = client.download_tab(tabname="Main")
                # … process zip …
            client.go_back_to_results()

Backward-compatible usage:
    client = NeubusClient()
    client.authenticate()          # alias for initialize()
    records = client.search(api)   # tries to find lease_number from API, then searches
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)

NEUBUS_BASE = "https://rrcsearch3.neubus.com"
PROFILE_ID = 17  # Oil and Gas Well Records


class NeubusAuthError(Exception):
    """Raised when Neubus authentication / initialization fails."""
    pass


class NeubusSearchError(Exception):
    """Raised when a Neubus search or file operation fails."""
    pass


class NeubusClient:
    """
    Client for Neubus TX RRC document archive.

    Uses Playwright browser for all operations because the Neubus API
    requires browser-generated Keycloak tokens that can't be replicated
    in pure Python.

    The SPA URL-param search works reliably: navigating to
        /search-profile?profileId=17&search_fields-lease_number=<lease>
    causes the SPA to execute the search and store results in the Vuex store.
    """

    NEUBUS_BASE = NEUBUS_BASE
    PROFILE_ID = PROFILE_ID

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._fs_url: Optional[str] = None
        self._initialized = False

    def initialize(self) -> None:
        """
        Launch a headless Chromium browser, navigate to the Neubus search
        profile page, and wait for Keycloak initialization to complete.

        Must be called before any search / file / download operations.

        Requires playwright: pip install playwright && playwright install chromium
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise NeubusAuthError(
                "Playwright is required for Neubus operations. "
                "Install with: pip install playwright && playwright install chromium"
            )

        logger.info("Initializing Neubus client via headless browser...")

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        self._page = self._context.new_page()
        self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # Navigate to search profile and wait for SPA + Keycloak init
        self._page.goto(
            f"{self.NEUBUS_BASE}/search-profile?profileId={self.PROFILE_ID}",
            wait_until="networkidle",
            timeout=30000,
        )

        try:
            self._page.wait_for_function(
                "() => window.keycloakInitCompleted === true", timeout=15000
            )
            logger.info("Neubus SPA: Keycloak init completed")
        except Exception as e:
            logger.warning(f"Neubus SPA: timed out waiting for keycloakInitCompleted: {e}")

        # Give the SPA a moment to fully settle
        self._page.wait_for_timeout(2000)

        # Extract FS_URL from the Vuex store env slice (used for downloads)
        self._fs_url = self._page.evaluate(
            """
            () => {
                const app = document.querySelector('#app');
                if (!app) return null;
                const store = app.__vue_app__
                    ? app.__vue_app__.config.globalProperties.$store
                    : (app.__vue__ ? app.__vue__.$store : null);
                if (!store) return null;
                const env = store.state.env || {};
                return env.FS_URL || env.fs_url || null;
            }
            """
        )

        self._initialized = True
        logger.info(f"Neubus client initialized, FS_URL={self._fs_url}")

    # Backward-compatible alias
    def authenticate(self) -> None:
        """Alias for initialize(). Provided for backward compatibility."""
        self.initialize()

    def close(self) -> None:
        """Close the browser and release Playwright resources."""
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._initialized = False

    def __enter__(self) -> "NeubusClient":
        self.initialize()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ──────────────────────────────────────────────────────────
    # Search
    # ──────────────────────────────────────────────────────────

    def search_by_lease(self, lease_number: str, district: str = "") -> List[Dict[str, Any]]:
        """
        Search Neubus for records by RRC lease number.

        Navigates the browser to the search URL with the lease_number param,
        waits for the SPA to execute the search, then extracts results from
        the Vuex store.

        Args:
            lease_number: RRC lease number (e.g., "15874")
            district: Optional RRC district filter (e.g., "08")

        Returns:
            List of record dicts with keys:
            - doc_id: Neubus document identifier (used for file listing)
            - lease_id: RRC lease number
            - lease_name: Lease name
            - operator: Operator name
            - county: County name
            - district: RRC district
            - field_name: Oil/gas field name
            - profile_type: Document profile type
            - allow_access: Whether access to files is permitted
            - _fields: Raw flattened image_fields dict
        """
        if not self._initialized:
            raise NeubusAuthError("Client not initialized. Call initialize() first.")

        params = f"search_fields-lease_number={lease_number}"
        if district:
            params += f"&search_fields-district={district}"

        url = f"{self.NEUBUS_BASE}/search-profile?profileId={self.PROFILE_ID}&{params}"
        logger.info(f"Searching Neubus: lease={lease_number} district={district or '(any)'}")

        self._page.goto(url, wait_until="networkidle", timeout=30000)
        # Allow the SPA store to populate after network idle
        self._page.wait_for_timeout(5000)

        # Extract results from Vuex store.
        # NOTE: Must use function() syntax, not arrow functions, to avoid $ escaping issues.
        js_extract = """
        () => {
            const app = document.querySelector('#app');
            if (!app) return {total: 0, images: []};
            const store = app.__vue_app__
                ? app.__vue_app__.config.globalProperties.$store
                : (app.__vue__ ? app.__vue__.$store : null);
            if (!store) return {total: 0, images: []};
            const sr = store.state.searchResult;
            const images = sr && sr.search_results ? sr.search_results.images || [] : [];
            return {
                total: store.state.totalResults || 0,
                images: images.map(function(img) {
                    var fields = {};
                    (img.image_fields || []).forEach(function(f) {
                        fields[f.field_name] = f.field_value;
                    });
                    return {
                        doc_id: img.doc_id,
                        allow_access: img.allow_access,
                        fields: fields,
                        tabs_count: (img.image_tabs || []).length
                    };
                })
            };
        }
        """
        result = self._page.evaluate(js_extract)
        total = result.get("total", 0)
        raw_images = result.get("images", [])

        records = self._parse_image_list(raw_images)

        # If the store page only holds a subset, try to load all pages
        if total > len(records):
            logger.info(
                f"Neubus: total={total} but only {len(records)} in store page, "
                "attempting to load full result set"
            )
            records = self._load_all_pages(total, records)

        logger.info(
            f"Neubus search_by_lease: lease={lease_number} -> "
            f"{len(records)} records (total reported={total})"
        )
        return records

    def search_by_api(self, api_number: str) -> List[Dict[str, Any]]:
        """
        Search Neubus for records by well API number using full-text search.

        Uses the api_ft (full-text) search field which matches against the
        PostgreSQL tsvector index. This finds records even when the exact
        api_number field is empty.

        Args:
            api_number: Well API number (any format — dashes stripped,
                padded to extract API8 = digits [2:10] of the 14-digit form).

        Returns:
            Same record dict format as search_by_lease().
        """
        if not self._initialized:
            raise NeubusAuthError("Client not initialized. Call initialize() first.")

        clean = api_number.replace("-", "").replace(" ", "")
        # Pad to 14 digits: API14 = SS CCC UUUUU DD ST
        if len(clean) < 14:
            clean = clean.ljust(14, "0")
        api8 = clean[2:10]  # county(3) + unique(5)

        url = f"{self.NEUBUS_BASE}/search-profile?profileId={self.PROFILE_ID}&search_fields-api_ft={api8}"
        logger.info(f"Searching Neubus by api_ft: api8={api8} (from {api_number})")

        self._page.goto(url, wait_until="networkidle", timeout=30000)
        self._page.wait_for_timeout(5000)

        # Use the same JS extraction as search_by_lease
        js_extract = """
        () => {
            const app = document.querySelector('#app');
            if (!app) return {total: 0, images: []};
            const store = app.__vue_app__
                ? app.__vue_app__.config.globalProperties.$store
                : (app.__vue__ ? app.__vue__.$store : null);
            if (!store) return {total: 0, images: []};
            const sr = store.state.searchResult;
            const images = sr && sr.search_results ? sr.search_results.images || [] : [];
            return {
                total: store.state.totalResults || 0,
                images: images.map(function(img) {
                    var fields = {};
                    (img.image_fields || []).forEach(function(f) {
                        fields[f.field_name] = f.field_value;
                    });
                    return {
                        doc_id: img.doc_id,
                        allow_access: img.allow_access,
                        fields: fields,
                        tabs_count: (img.image_tabs || []).length
                    };
                })
            };
        }
        """
        result = self._page.evaluate(js_extract)
        total = result.get("total", 0)
        raw_images = result.get("images", [])

        records = self._parse_image_list(raw_images)

        if total > len(records):
            records = self._load_all_pages(total, records)

        logger.info(f"Neubus search_by_api: api8={api8} -> {len(records)} records (total={total})")
        return records

    def search(self, api_number: str) -> List[Dict[str, Any]]:
        """
        Backward-compatible search by API number.

        First tries searching Neubus directly by api_ft (full-text on API).
        If that returns no results, falls back to resolving the lease number
        from WellRegistry and searching by lease.
        """
        if not self._initialized:
            raise NeubusAuthError("Client not initialized. Call initialize() first.")

        # Try direct API search first (no DB lookup needed)
        records = self.search_by_api(api_number)
        if records:
            return records

        # Fallback: resolve lease number from DB
        from apps.public_core.services.neubus_ingest import _lookup_lease_number
        lease_number = _lookup_lease_number(api_number)
        return self.search_by_lease(lease_number)

    # ──────────────────────────────────────────────────────────
    # File listing (browser UI approach)
    # ──────────────────────────────────────────────────────────

    def get_record_files(self, record_index: int) -> List[Dict[str, Any]]:
        """
        Click a search result row and extract file listings from all tabs.

        The `getTabFoldersFiles` API endpoint returns empty data until the
        record panel is actually opened in the browser UI. This method clicks
        the table row at ``record_index``, waits for the Vue components inside
        the expanded panel to finish loading, then walks every DOM element
        looking for Vue 2 instances whose ``$data.fileData.files`` array is
        populated — which is the reliable signal that file metadata is ready.

        Must be called while search results are displayed on the page (i.e.
        after ``search_by_lease()`` and before ``go_back_to_results()``).

        Args:
            record_index: Zero-based index of the row to click in the results
                table (matches the ``_index`` value on record dicts returned by
                ``search_by_lease()``).

        Returns:
            List of file dicts, one entry per file across ALL tabs:
            - nuid: str — Neubus unique file identifier
            - name: str — original filename (e.g. "689_17-4328543.pdf")
            - format: str — file extension / format code
            - file_size: int — size in bytes (0 if unknown)
            - uploaded_on: str — upload date string
            - tabname: str — tab label this file belongs to
            - box_type: str — "B" (document) or "W" (well log)
            - image_id: str — Neubus image identifier
            - well_number: str — prefix parsed from filename
        """
        if not self._initialized:
            raise NeubusAuthError("Client not initialized. Call initialize() first.")

        # Click the correct row in the results table. Each data row has the
        # CSS class `nde-table-body-row`; click the one at record_index.
        js_click_row = """
        (idx) => {
            var rows = document.querySelectorAll('.nde-table-body-row');
            if (!rows || rows.length === 0) {
                // Fallback: try generic table body rows
                rows = document.querySelectorAll('table tbody tr');
            }
            if (idx >= rows.length) return false;
            rows[idx].click();
            return true;
        }
        """
        clicked = self._page.evaluate(js_click_row, record_index)
        if not clicked:
            logger.warning(
                f"[Neubus] get_record_files: could not find row {record_index} "
                "in results table"
            )
            return []

        logger.info(
            f"[Neubus] get_record_files: clicked row {record_index}, "
            "waiting for panel to expand…"
        )

        # Wait for the record detail panel to appear.
        try:
            self._page.wait_for_selector(".nde-view-record-item", timeout=8000)
        except Exception:
            logger.warning(
                "[Neubus] get_record_files: panel selector .nde-view-record-item "
                "did not appear within 8 s"
            )

        # Extra settle time so Vue components finish their async data fetch.
        self._page.wait_for_timeout(5000)

        # Walk ALL DOM elements for Vue 2 instances that carry fileData.files.
        # This is more reliable than recursive $children traversal because
        # functional/abstract components may not appear in $children.
        js_extract_files = """
        () => {
            var results = [];
            var els = document.querySelectorAll('*');
            for (var i = 0; i < els.length; i++) {
                var el = els[i];
                if (!el.__vue__) continue;
                var vm = el.__vue__;
                if (!vm.$data) continue;
                var fd = vm.$data.fileData;
                if (!fd || typeof fd !== 'object') continue;
                var files = fd.files;
                if (!Array.isArray(files) || files.length === 0) continue;
                var tabName = (vm.$props && vm.$props.tabName) ? vm.$props.tabName : '';
                var docId   = (vm.$props && vm.$props.doc_id)  ? vm.$props.doc_id  : '';
                for (var j = 0; j < files.length; j++) {
                    var f = files[j];
                    results.push({
                        nuid:        f.nuid        || '',
                        name:        f.name        || '',
                        format:      f.format      || '',
                        file_size:   f.file_size   || 0,
                        uploaded_on: f.uploaded_on || f.create_on || '',
                        tabname:     f.tabname     || tabName,
                        box_type:    fd.box_type   || '',
                        image_id:    fd.image_id   || '',
                        doc_id:      docId
                    });
                }
            }
            return results;
        }
        """
        raw_files = self._page.evaluate(js_extract_files)

        if not raw_files:
            # Check whether the panel says "No Files Uploaded"
            no_files_text = self._page.evaluate(
                "() => !!document.querySelector('.nde-view-record-item') && "
                "document.querySelector('.nde-view-record-item').innerText"
                ".includes('No Files Uploaded')"
            )
            if no_files_text:
                logger.info(
                    f"[Neubus] get_record_files: row {record_index} — "
                    "panel open but no files uploaded"
                )
            else:
                logger.warning(
                    f"[Neubus] get_record_files: row {record_index} — "
                    "no fileData components found; panel may not have loaded"
                )
            return []

        files: List[Dict[str, Any]] = []
        seen_nuids: set = set()
        for f in raw_files:
            nuid = f.get("nuid", "")
            if nuid in seen_nuids:
                continue
            seen_nuids.add(nuid)
            name = f.get("name", "")
            well_number = name.split("_")[0] if name and "_" in name else ""
            files.append({
                "nuid":        nuid,
                "name":        name,
                "format":      f.get("format", ""),
                "file_size":   f.get("file_size", 0),
                "uploaded_on": f.get("uploaded_on", ""),
                "tabname":     f.get("tabname", ""),
                "box_type":    f.get("box_type", ""),
                "image_id":    f.get("image_id", ""),
                "doc_id":      f.get("doc_id", ""),
                "well_number": well_number,
            })

        logger.info(
            f"[Neubus] get_record_files: row {record_index} → {len(files)} files"
        )
        return files

    # ──────────────────────────────────────────────────────────
    # Download (browser-triggered zip via expect_download)
    # ──────────────────────────────────────────────────────────

    def dismiss_swal(self) -> bool:
        """
        Check for and dismiss any SweetAlert2 overlay dialog.

        SweetAlert2 popups can block interaction with the underlying page.
        This method looks for the overlay and clicks the confirm/OK button,
        or presses Escape as a fallback.

        Returns:
            True if a SweetAlert2 dialog was found and dismissed; False otherwise.
        """
        if not self._initialized:
            return False

        try:
            has_swal = self._page.evaluate("""
                () => {
                    var swal = document.querySelector('.swal2-container, .swal2-popup, .swal2-shown');
                    return !!swal;
                }
            """)
            if not has_swal:
                return False

            logger.info("[Neubus] dismiss_swal: SweetAlert2 dialog detected, dismissing…")

            # Try clicking the confirm/OK button first
            dismissed = self._page.evaluate("""
                () => {
                    var btn = document.querySelector(
                        '.swal2-confirm, .swal2-actions button, .swal2-popup button'
                    );
                    if (btn) { btn.click(); return true; }
                    return false;
                }
            """)
            if dismissed:
                self._page.wait_for_timeout(500)
                return True

            # Fallback: press Escape
            self._page.keyboard.press("Escape")
            self._page.wait_for_timeout(500)
            logger.info("[Neubus] dismiss_swal: dismissed via Escape key")
            return True
        except Exception as e:
            logger.warning(f"[Neubus] dismiss_swal: error: {e}")
            return False

    def download_tab(self, tabname: str = "Main") -> Tuple[Path, int]:
        """
        Download all files in a tab as a zip by clicking Actions → Download Tab.

        Must be called AFTER ``get_record_files()`` so the record panel is
        already open.  The FS server (rrcsearch3fs.neubus.com) is WAF-protected
        and returns 403 to direct Python requests, so the download MUST be
        triggered through the browser.

        Playwright's ``expect_download()`` context manager intercepts the
        browser-initiated blob-URL download and saves it to a temp file.

        The method finds the correct tab section by matching the tabname text
        within ``.nde-view-record-item`` sections, skipping any that show
        "No Files Uploaded". If the target tab is not found or has no files,
        it falls back to any tab that does have files.

        Args:
            tabname: Name of the tab to download (e.g., "Main", "Document",
                "Well Log"). Defaults to "Main".

        Returns:
            (path_to_zip, size_in_bytes) — caller is responsible for deleting
            the zip after extraction.

        Raises:
            NeubusSearchError: If no tab with files can be found, the Actions
                menu or Download Tab option cannot be found, or if the download
                does not start within the timeout.
        """
        if not self._initialized:
            raise NeubusAuthError("Client not initialized. Call initialize() first.")

        # Dismiss any SweetAlert2 overlay that might block interaction
        self.dismiss_swal()

        # DOM structure (from browser inspection):
        # One .nde-view-record-item (v-card) wraps ALL sub-tabs.
        # Inside it: multiple div.pl-10.pr-6 containers, one per sub-tab.
        # Each sub-tab container has:
        #   - A button[aria-label="<Type> - <Name>"] (e.g. "Well Log -  Main")
        #   - An Actions button (button[aria-label="Menu"] with text "Actions")
        #   - Either "No Files Uploaded" text or a file table
        #
        # We find the right Actions button by locating the sub-tab container
        # whose aria-label matches the target tabname and has files.
        js_find_and_click = """
        (targetTab) => {
            // Each sub-tab lives in a div.pl-10.pr-6 container
            var containers = document.querySelectorAll('.nde-view-record-item .pl-10.pr-6');
            if (!containers.length) return {error: 'no_subtab_containers', count: 0};

            var tabs = [];
            var targetIdx = -1;
            var fallbackIdx = -1;

            for (var i = 0; i < containers.length; i++) {
                var c = containers[i];
                var text = c.innerText || '';
                var hasNoFiles = text.includes('No Files Uploaded');

                // Get the tab label from the aria-label of the tab-name button
                var labelBtn = c.querySelector('button[aria-label]');
                var label = labelBtn ? (labelBtn.getAttribute('aria-label') || '') : '';

                // Find the Actions button in this container
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

                if (hasNoFiles || !actionBtn) continue;

                // Check if this tab matches the target
                if (label.toLowerCase().includes(targetTab.toLowerCase())) {
                    if (targetIdx < 0) targetIdx = i;
                }
                if (fallbackIdx < 0) fallbackIdx = i;
            }

            var chosenIdx = targetIdx >= 0 ? targetIdx : fallbackIdx;
            if (chosenIdx < 0) return {error: 'no_tab_with_files', tabs: tabs};

            // Click the Actions button in the chosen container
            var chosen = containers[chosenIdx];
            var btns2 = chosen.querySelectorAll('button');
            for (var k = 0; k < btns2.length; k++) {
                if ((btns2[k].innerText || '').trim().toLowerCase().startsWith('actions')) {
                    btns2[k].click();
                    return {
                        clicked: true,
                        chosenIdx: chosenIdx,
                        chosenLabel: tabs[chosenIdx].label,
                        wasTarget: chosenIdx === targetIdx,
                        tabs: tabs
                    };
                }
            }
            return {error: 'action_btn_not_clickable', chosenIdx: chosenIdx};
        }
        """
        result = self._page.evaluate(js_find_and_click, tabname)

        if "error" in result:
            raise NeubusSearchError(
                f"[Neubus] download_tab: {result['error']} "
                f"(target='{tabname}', tabs={result.get('tabs', [])})"
            )

        chosen_label = result.get("chosenLabel", "?")
        if result.get("wasTarget"):
            logger.info(f"[Neubus] download_tab: clicked Actions for '{chosen_label}'")
        else:
            logger.info(
                f"[Neubus] download_tab: target '{tabname}' not found/empty, "
                f"fell back to '{chosen_label}'"
            )
        self._page.wait_for_timeout(500)

        # Find the "Download Tab" menu item in the now-open dropdown.
        # Vuetify uses .v-list-item elements for menu items.
        download_item = self._page.query_selector(
            ".v-list-item:has-text('Download Tab'), "
            "li:has-text('Download Tab'), "
            "a:has-text('Download Tab'), "
            "[class*='dropdown'] :has-text('Download Tab')"
        )
        if not download_item:
            # Close any open dropdown and raise.
            self._page.keyboard.press("Escape")
            self.dismiss_swal()
            raise NeubusSearchError(
                "[Neubus] download_tab: 'Download Tab' menu item not found "
                "after opening Actions menu"
            )

        # Create a temp file to receive the download.
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp.close()
        dest = Path(tmp.name)

        logger.info("[Neubus] download_tab: clicking 'Download Tab', expecting download…")

        try:
            with self._page.expect_download(timeout=60000) as dl_info:
                download_item.click()
            download = dl_info.value
            download.save_as(str(dest))
        except Exception as exc:
            dest.unlink(missing_ok=True)
            self.dismiss_swal()
            raise NeubusSearchError(
                f"[Neubus] download_tab: download did not complete: {exc}"
            ) from exc

        # Dismiss any post-download SweetAlert2 dialog
        self.dismiss_swal()

        size = dest.stat().st_size
        logger.info(f"[Neubus] download_tab: saved zip to {dest} ({size:,} bytes)")
        return dest, size

    def set_page_size(self, size: int) -> bool:
        """
        Update the SPA table's page size to show more rows at once.

        After search_by_lease(), the table shows 10 rows per page by default.
        This method triggers a new search with a larger pageSize so more rows
        are visible and accessible for clicking (via get_record_files).

        The Vuex store is updated with the new page size, and a searchImages
        dispatch is triggered to reload the table with the expanded view.

        Args:
            size: New page size (typically len(all_records) or min(len(all_records), 500))

        Returns:
            True if the page size was successfully updated; False otherwise.
        """
        if not self._initialized:
            logger.warning("[Neubus] set_page_size called before initialization")
            return False

        js = """
        async (newSize) => {
            const app = document.querySelector('#app');
            if (!app) return false;

            const store = app.__vue_app__
                ? app.__vue_app__.config.globalProperties.$store
                : (app.__vue__ ? app.__vue__.$store : null);
            if (!store) return false;

            const neusearch = Object.assign(
                {},
                store.state.oldNeusearch || store.state.Neusearch || {}
            );
            neusearch.pageSize = newSize;
            neusearch.page = 1;

            try {
                await store.dispatch('searchImages', neusearch);
                return true;
            } catch (e) {
                return false;
            }
        }
        """
        try:
            result = self._page.evaluate(js, size)
            if result:
                # Wait for the table to re-render with new page size
                self._page.wait_for_timeout(3000)
                logger.info(f"[Neubus] set_page_size: changed to {size}")
            else:
                logger.warning(f"[Neubus] set_page_size({size}): dispatch failed")
            return result
        except Exception as e:
            logger.error(f"[Neubus] set_page_size({size}): {e}")
            return False

    def get_visible_row_count(self) -> int:
        """Return the number of visible data rows in the results table."""
        count = self._page.evaluate("""
            () => {
                var rows = document.querySelectorAll('.nde-table-body-row');
                if (!rows || rows.length === 0) {
                    rows = document.querySelectorAll('table tbody tr');
                }
                return rows ? rows.length : 0;
            }
        """)
        return count or 0

    def go_to_next_page(self) -> bool:
        """
        Click the next-page button in the table pagination.

        Returns True if a next page exists and was clicked; False if already
        on the last page or the button could not be found.
        """
        js = """
        () => {
            // Try Vuetify v-pagination next button
            var btns = document.querySelectorAll(
                '.v-pagination button, .v-data-footer button, .nde-table-footer button'
            );
            for (var i = 0; i < btns.length; i++) {
                var b = btns[i];
                var aria = b.getAttribute('aria-label') || '';
                var text = b.innerText || '';
                // Look for "next" or ">" or chevron-right icon
                if (aria.toLowerCase().includes('next') || text.includes('\u203a') ||
                    text.includes('>') ||
                    b.querySelector(
                        '.mdi-chevron-right, .v-icon--right, [class*="chevron-right"]'
                    )) {
                    if (!b.disabled) {
                        b.click();
                        return true;
                    }
                    return false;  // button exists but disabled (last page)
                }
            }
            return false;
        }
        """
        try:
            result = self._page.evaluate(js)
            if result:
                self._page.wait_for_timeout(3000)  # Wait for table to re-render
                logger.info("[Neubus] go_to_next_page: navigated to next page")
            return bool(result)
        except Exception as e:
            logger.warning(f"[Neubus] go_to_next_page failed: {e}")
            return False

    def go_back_to_results(self) -> None:
        """
        Click the 'Search Results' back link to close the record panel and
        return to the search results list.

        The link uses the CSS class ``.nde-view-record-goback``.  If it is not
        found, the method logs a warning but does not raise so the caller can
        continue processing.
        """
        if not self._initialized:
            return

        back_link = self._page.query_selector(".nde-view-record-goback")
        if back_link:
            back_link.click()
            self._page.wait_for_timeout(1000)
            logger.debug("[Neubus] go_back_to_results: returned to search results")
        else:
            logger.warning(
                "[Neubus] go_back_to_results: .nde-view-record-goback not found; "
                "panel may already be closed"
            )

    # ──────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────

    def _parse_image_list(self, raw_images: list) -> List[Dict[str, Any]]:
        """
        Convert raw image dicts (with fields already flattened) to record dicts.

        Each record includes ``_index`` — the zero-based position of this record
        in the displayed results table — so callers can pass it directly to
        ``get_record_files(record["_index"])``.
        """
        records = []
        for i, img in enumerate(raw_images):
            fields = img.get("fields", {})
            records.append({
                "doc_id": img.get("doc_id", ""),
                "lease_id": fields.get("lease_number", ""),
                "lease_name": fields.get("lease_name", ""),
                "operator": fields.get("operator_name", ""),
                "county": fields.get("county", ""),
                "district": fields.get("district", ""),
                "field_name": fields.get("field_name", ""),
                "profile_type": fields.get("profile_type", ""),
                "allow_access": img.get("allow_access", True),
                "_fields": fields,
                "_index": i,  # row index for get_record_files()
            })
        return records

    def _load_all_pages(
        self, total: int, initial_records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Attempt to load the full result set when more pages exist.

        Uses the browser's axios instance to call /getSearchImages with a
        larger pageSize so all records are retrieved in one shot (up to 500).
        Falls back to the initial partial list if this fails.
        """
        js_load_all = """
        async () => {
            const app = document.querySelector('#app');
            if (!app) return [];
            const store = app.__vue_app__
                ? app.__vue_app__.config.globalProperties.$store
                : (app.__vue__ ? app.__vue__.$store : null);
            if (!store) return [];

            const base = store.state.oldNeusearch || store.state.Neusearch || {};
            const neusearch = Object.assign({}, base, {
                page: 1,
                pageSize: Math.min(TOTAL_PLACEHOLDER, 500)
            });

            try {
                const response = await axios.post('/getSearchImages', neusearch);
                const d = response.data;
                const sr = (d.data && d.data.data && d.data.data.search_results)
                    ? d.data.data.search_results
                    : (d.data && d.data.search_results ? d.data.search_results : null);
                if (!sr) return [];
                const images = sr.images || [];
                return images.map(function(img) {
                    var fields = {};
                    (img.image_fields || []).forEach(function(f) {
                        fields[f.field_name] = f.field_value;
                    });
                    return {
                        doc_id: img.doc_id,
                        allow_access: img.allow_access,
                        fields: fields
                    };
                });
            } catch(e) {
                return [];
            }
        }
        """.replace("TOTAL_PLACEHOLDER", str(total))

        try:
            all_images = self._page.evaluate(js_load_all)
            if all_images:
                return self._parse_image_list(all_images)
        except Exception as e:
            logger.warning(f"Neubus _load_all_pages failed: {e}")

        return initial_records

    @staticmethod
    def _normalize_api(api_number: str) -> str:
        """
        Normalize an API number to the 14-digit format.

        Strips dashes and spaces, then zero-pads on the right to 14 characters.
        Example: "42-003-35663" -> "42003356630000"
        """
        clean = api_number.replace("-", "").replace(" ", "")
        return clean.ljust(14, "0")[:14]
