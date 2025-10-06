"""
Agentic ingestion app (Playwright/Chromium-driven web processing, OCR orchestration, and normalization).

This app will encapsulate:
- Headless acquisition (e.g., RRC) for a given API
- OCR backends coordination (Google Vision, AWS Textract, fallbacks)
- Normalization to evolving JSON schemas and findings

App config defined in apps.AgenticIngestConfig
"""

default_app_config = "apps.agentic_ingest.apps.AgenticIngestConfig"


