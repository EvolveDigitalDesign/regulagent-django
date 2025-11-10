# RegulAgent Django Logic Tree

A structured map of `regulagent-django/` showing each directory’s overall goal, how subdirectories contribute, and key interactions between components.

- regulagent-django — Overall goal: Django-based backend for RegulatoryAgent. Hosts API endpoints, RRC extraction, PDF→JSON extraction, planning kernel, materials, and policy overlays.
  - RegulAgentV2 — Goal: Application source tree (project code, apps, docs, runtime artifacts).
    - regulagent-backend — Goal: Django project root (settings, URLs, media/static, apps, orchestration).
      - manage.py — Django management entrypoint (migrate, runserver, custom commands).
      - Makefile / requirements/ — Build/run helpers and pinned dependencies (base/dev/prod).
      - docker — Goal: Containerization for dev/prod.
        - compose.dev.yml / compose.prod.yml — Compose services (web/db, etc.).
        - Dockerfile / Dockerfile.db — App and DB images.
        - entrypoint.sh — Container bootstrap.
      - ra_config — Goal: Project configuration and web surface.
        - settings/ — Base, development, production settings (DB, media/static roots, debug, etc.).
        - urls.py — Global routing. Wires APIs from `apps/*/views` (public_core, kernel, tenant_overlay, policy_ingest).
        - asgi.py / wsgi.py — Server entrypoints.
        - mediafiles/ — Persisted artifacts (notably `rrc/completions/<api>/` RRC PDFs).
        - staticfiles/ — Admin/browsable API assets.
        - tmp/ — Transient outputs (e.g., `tmp/extractions/W3A_<api>_plan.json`).
      - apps — Goal: Business-domain Django apps (models, services, views, commands, tests).
        - public_core — Goal: RRC document fetching, PDF classification/extraction, persistence, orchestration.
          - models/
            - ExtractedDocument — Stores per-document JSON, provenance (`source_path`), status/errors.
            - WellRegistry — Well metadata and API association.
          - services/
            - rrc_completions_extractor.py — Headless RRC fetcher (cache-aware 14 days). Produces file manifest; saves PDFs under mediafiles.
            - openai_extraction.py — Classifies PDFs (W‑2/W‑15/GAU/…) and extracts structured JSON; optional embeddings.
          - views/
            - rrc_extractions.py — API to fetch + extract + persist (standalone extraction endpoint).
            - w3a_from_api.py — End-to-end API: RRC fetch → classify/extract → persist → plan; returns plan + `extraction` meta.
          - management/commands/
            - get_W3A_from_api.py — CLI to perform the same E2E flow and write plan JSON to tmp.
            - extract_local_rrc.py — Helpers for local artifact extraction (when present).
          - Interactions — Called by API/CLI; persists facts for kernel; reads/writes mediafiles and tmp.
        - kernel — Goal: Deterministic W‑3A planner (facts + policy → compliant, explainable steps with citations and materials).
          - services/
            - policy_kernel.py — Planning pipeline: baseline steps, mechanical awareness (CIBP/PACKER/DV), exposure-based CIBP at top-of-interval − 10 ft, cap, district overrides, explicit overrides, overlap suppression, tagging enrichment, defaults + materials, optional long-plug merge, export mapping.
            - w3a_rules.py — W‑3A baseline rule scaffold (shoe coverage, productive isolation, etc.).
            - violations.py — Violation codes/messages surfaced in plan outputs.
            - policy_registry.py — Integration registry (when applicable).
          - views/
            - plan_preview.py — Preview plans from tenant/engagement facts (bypass extraction path).
            - advisory.py — Advisory/sanity endpoints.
          - tests/ — Golden tests and scenario coverage.
          - Interactions — Consumes facts (public_core or tenant_overlay) and effective policy (policy.loader); calls materials engine.
        - materials — Goal: Volumetric/materials computations for steps.
          - services/
            - material_engine.py — Annulus/cylinder capacities, balanced plug math, squeeze volumes, sack computation, rounding.
          - tests/ — Unit/scenario volumetrics tests.
          - Interactions — Called by kernel to attach materials (slurry, spacers) to steps.
        - policy — Goal: Policy packs/overlays and loader.
          - packs/
            - tx/ — TX overlays by district/county/field; schema.json for validation.
            - tx_rrc_w3a_base_policy_pack.yaml — Base preferences/knobs (e.g., `cement_above_cibp_min_ft`).
          - services/
            - loader.py — `get_effective_policy(district, county, field)`; merges base + overlays; exposes preferences.
            - district_overlay_builder.py — Programmatic overlays (e.g., tagging hints, formation-top plugs).
            - policy_applicator.py — Apply overlays where used.
            - validate_overlays.py — Validate overlay structure/content.
          - management/commands/ — Import/mine policy knobs; admin utilities.
          - Interactions — Kernel queries loader for effective policy/preferences.
        - policy_ingest — Goal: DB-backed policy ingestion/inspection APIs.
          - models/ — `policy_rule`, `policy_section`.
          - management/commands/ — Ingest/mining tools for TX W‑3A knobs.
          - views/ / urls.py — APIs to browse/validate policy content.
          - Interactions — Supports structured policy curation consumed by overlays/loader.
        - tenant_overlay — Goal: Tenant/engagement-specific facts resolution and preview endpoints.
          - services/ — Resolve engagement facts.
          - views/ — API for resolved facts.
          - Interactions — Alternative facts source to run kernel without extraction.
      - README*.md — Architecture docs (includes `regulagent-backend/README-w3a-api.md`).
      - tmp/ — Example inputs/outputs (e.g., approved W‑3A PDFs) for demos/tests.

---

## Primary Runtime Flow (API)

1) `POST /api/plans/w3a/from-api` → `apps/public_core/views/w3a_from_api.W3AFromApiView`
- Validate inputs; normalize API (digits; last 8 for RRC search).
- RRC fetch (`public_core.services.rrc_completions_extractor.extract_completions_all_documents`) with cache; downloads W‑2/W‑15/GAU PDFs.
- Classify + extract (`public_core.services.openai_extraction`); persist `ExtractedDocument` rows.
- Optional GAU override (JSON or PDF) if system GAU invalid/missing; persist GAU.
- Build facts from latest W‑2/W‑15/GAU; load effective policy (`policy.services.loader.get_effective_policy`).
- Plan via kernel (`kernel.services.policy_kernel.plan_from_facts`): baseline steps → mechanical awareness → CIBP at top-of-interval − 10 ft + cap (policy-driven cap length) → overrides → overlap suppression → tagging → defaults + materials → optional long-plug merge.
- Return plan JSON: `steps`, totals, `violations`, `rrc_export` (labels CIBP/CIBP cap), and `extraction` meta (`status`, `source`, `output_dir`, `files`).

---

## Key Interactions (Call Graph)

- API (`w3a_from_api`) → RRC extractor → classification/extraction → ExtractedDocument → policy loader → kernel → materials engine → export mapping.
- Kernel → w3a_rules (baseline) → district overrides/explicit overrides → materials engine → export mapping.
- Policy loader → packs/overlays + builder → preferences/knobs to kernel.

---

## Data Lifecycle (Simplified)

RRC PDFs → JSON extraction → Persist (`ExtractedDocument`) → Facts → Policy → Kernel Plan → Materials → Merge (optional) → Export.
