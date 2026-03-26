import hashlib
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag  # type: ignore
from django.core.management.base import BaseCommand, CommandParser


SOURCE_URL = "https://www.srca.nm.gov/parts/title19/19.015.0025.html"

# Section number pattern inside NMAC 19.15.25
SECTION_PAT = re.compile(r"19\.15\.25\.(\d+)")

topic_map: Dict[str, str] = {
    'nm.nmac.19.15.25.1': 'admin_issuing_agency',
    'nm.nmac.19.15.25.2': 'admin_scope',
    'nm.nmac.19.15.25.3': 'admin_statutory_authority',
    'nm.nmac.19.15.25.4': 'admin_duration',
    'nm.nmac.19.15.25.5': 'admin_effective_date',
    'nm.nmac.19.15.25.6': 'admin_objective',
    'nm.nmac.19.15.25.7': 'definitions',
    'nm.nmac.19.15.25.8': 'plugging_requirements',
    'nm.nmac.19.15.25.9': 'plugging_notice',
    'nm.nmac.19.15.25.10': 'plugging',
    'nm.nmac.19.15.25.11': 'plugging_reports',
    'nm.nmac.19.15.25.12': 'temporary_abandonment',
    'nm.nmac.19.15.25.13': 'temporary_abandonment_permit',
    'nm.nmac.19.15.25.14': 'mechanical_integrity',
    'nm.nmac.19.15.25.15': 'fresh_water_wells',
}


@dataclass
class ParsedSection:
    path: str
    heading: str
    text: str
    anchor: str
    order_idx: int


@dataclass
class ParsedRule:
    num: str
    rule_id: str
    citation: str
    title: str
    sections: List[ParsedSection]


def fetch_html(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _is_bold(tag: Tag) -> bool:
    """Return True if the tag contains bold/strong child elements."""
    return bool(tag.find(["b", "strong"]))


def _bold_text(tag: Tag) -> str:
    """Return concatenated text from bold/strong descendants."""
    parts = []
    for child in tag.find_all(["b", "strong"]):
        t = child.get_text(" ", strip=True)
        if t:
            parts.append(t)
    return " ".join(parts)


def parse_subsections(paragraphs: List[str]) -> List[ParsedSection]:
    """Parse a list of text paragraphs into ordered subsections.

    NM NMAC uses:
      Level 0 — uppercase letters: (A), (B), (C)
      Level 1 — digits: (1), (2), (3)
      Level 2 — lowercase letters: (a), (b), (c)

    This mirrors the nesting logic from fetch_tx_ch3.parse_rule_sections but
    adapted to NM's ordering convention.
    """
    # NM subsection marker pattern — matches both (A) and A. formats
    pat = re.compile(r"^(?:\((?P<tok>[A-Z]|[0-9]+|[a-z])\)|(?P<dot_tok>[A-Z]|[0-9]+|[a-z])\.)\s*")

    sections: List[ParsedSection] = []
    stack: List[str] = []
    buf_text: List[str] = []

    def flush(order_idx: int) -> None:
        if not stack:
            return
        path = stack[0]
        for tok in stack[1:]:
            path += f"({tok})"
        text = "\n".join(buf_text).strip()
        if not text:
            return
        sections.append(
            ParsedSection(
                path=path,
                heading="",
                text=text,
                anchor="",
                order_idx=order_idx,
            )
        )

    order = 0
    for text in paragraphs:
        if not text:
            continue
        m = pat.match(text)
        if m:
            tok = m.group("tok") or m.group("dot_tok")
            # NM level ordering: A-Z = 0, digits = 1, a-z = 2
            if tok.isalpha() and tok.isupper():
                level = 0
            elif tok.isdigit():
                level = 1
            else:
                level = 2

            if stack:
                while len(stack) > level + 1:
                    stack.pop()
                if len(stack) == level + 1:
                    flush(order)
                    order += 1
                    buf_text = []

            while len(stack) < level + 1:
                stack.append("")
            stack[level] = tok
            del stack[level + 1:]

            remainder = text[m.end():].strip()
            if remainder:
                buf_text.append(remainder)
            continue

        if stack:
            buf_text.append(text)

    flush(order)

    # If no subsection markers found, create a single section with all text
    if not sections:
        full_text = "\n".join(p for p in paragraphs if p).strip()
        if full_text:
            sections.append(
                ParsedSection(
                    path="",
                    heading="",
                    text=full_text,
                    anchor="",
                    order_idx=0,
                )
            )

    return sections


def parse_nm_page(html: str) -> List[ParsedRule]:
    """Parse the single NMAC 19.15.25 Word-exported HTML page into ParsedRule objects.

    Strategy:
    1. Walk all <p> elements in document order.
    2. When a <p> contains bold text matching '19.15.25.N', start a new rule.
    3. Collect subsequent <p> text lines until the next section boundary.
    4. Feed collected lines to parse_subsections().
    """
    soup = BeautifulSoup(html, "html.parser")

    # Gather all paragraph-like nodes in document order
    paragraphs = soup.find_all("p")

    rules: List[ParsedRule] = []
    current_num: Optional[str] = None
    current_title: str = ""
    current_lines: List[str] = []

    def flush_rule() -> None:
        if current_num is None:
            return
        rule_id = f"nm.nmac.19.15.25.{current_num}"
        sections = parse_subsections(current_lines)
        rules.append(
            ParsedRule(
                num=current_num,
                rule_id=rule_id,
                citation=f"NMAC 19.15.25.{current_num}",
                title=current_title,
                sections=sections,
            )
        )

    # Track seen rule numbers to merge duplicates
    seen_rules: Dict[str, int] = {}  # num -> index in rules list

    for p in paragraphs:
        full_text = p.get_text(" ", strip=True)
        if not full_text:
            continue

        # Skip NMAC history/amendment notes like "[NMAC - Rp, 19.15.4.x ...]"
        stripped = full_text.lstrip()
        if stripped.startswith("[") and "NMAC" in stripped:
            continue

        # Check if this paragraph announces a new section via bold text
        bold = _bold_text(p)
        m = SECTION_PAT.search(bold) if bold else None
        if not m and _is_bold(p):
            # Also try full paragraph text but only if paragraph has bold elements
            m = SECTION_PAT.search(full_text)

        if m:
            new_num = m.group(1)

            # If this section was already seen, merge content into it
            if new_num in seen_rules:
                # Don't flush — just switch to appending to the existing rule's lines
                flush_rule()
                # Point current_num to existing rule for continued collection
                current_num = new_num
                current_lines = []  # will be merged on next flush
                # Extract any title-like text from this paragraph
                after = SECTION_PAT.sub("", bold or full_text, count=1).strip()
                after = re.sub(r"^[\s.\-–:]+", "", after).strip()
                if after and not rules[seen_rules[new_num]].title:
                    rules[seen_rules[new_num]].title = after
                continue

            # Flush previous rule
            flush_rule()
            current_num = new_num
            # Extract title: text after the section number pattern
            after = SECTION_PAT.sub("", bold or full_text, count=1).strip()
            # Strip leading punctuation/whitespace like ". TITLE"
            after = re.sub(r"^[\s.\-–:]+", "", after).strip()
            current_title = after
            current_lines = []
            continue

        if current_num is not None:
            current_lines.append(full_text)

    flush_rule()

    # Merge any duplicate rules by combining their sections
    merged: Dict[str, ParsedRule] = {}
    for rule in rules:
        if rule.num in merged:
            # Reindex appended sections to continue from the last order_idx
            offset = len(merged[rule.num].sections)
            for s in rule.sections:
                s.order_idx += offset
            merged[rule.num].sections.extend(rule.sections)
            # Use first non-empty title
            if not merged[rule.num].title and rule.title:
                merged[rule.num].title = rule.title
        else:
            merged[rule.num] = rule

    return list(merged.values())


class Command(BaseCommand):
    help = (
        "Fetch and parse NMAC 19.15.25 (Plugging and Abandonment) from the NM SRCA website; "
        "print summary or upsert when --write is used."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--rule",
            dest="rule",
            help="Limit to a specific rule_id like nm.nmac.19.15.25.10",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            help="Persist to DB (PolicyRule/PolicySection)",
        )
        parser.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            help="Print summaries even when --write is used",
        )
        parser.add_argument(
            "--version-tag",
            dest="version_tag",
            default="manual",
            help="Version tag to apply (e.g., 2025-Q4)",
        )

    def handle(self, *args, **options) -> None:
        from apps.policy_ingest.models import PolicyRule, PolicySection

        version_tag: str = options["version_tag"]
        limit_rule: Optional[str] = options.get("rule")
        do_write: bool = options.get("write", False)
        do_dry: bool = options.get("dry_run", False)

        self.stdout.write(f"Fetching {SOURCE_URL} ...")
        page_html = fetch_html(SOURCE_URL)
        html_sha = hashlib.sha256(page_html.encode("utf-8")).hexdigest()
        self.stdout.write(f"Fetched {SOURCE_URL} sha={html_sha[:12]}")

        parsed_rules = parse_nm_page(page_html)
        self.stdout.write(f"Discovered {len(parsed_rules)} sections in NMAC 19.15.25")

        if limit_rule:
            parsed_rules = [r for r in parsed_rules if r.rule_id == limit_rule]

        for pr in parsed_rules:
            self.stdout.write(f"  - {pr.rule_id} :: {pr.title or '(no title)'} ({len(pr.sections)} subsections)")

            if do_dry or not do_write:
                for s in pr.sections[:5]:
                    self.stdout.write(
                        f"      {s.order_idx:03d} {s.path} :: {s.text[:80]}"
                    )
                if not do_write:
                    continue

            topic = topic_map.get(pr.rule_id)

            rule_obj, _ = PolicyRule.objects.update_or_create(
                rule_id=pr.rule_id,
                version_tag=version_tag,
                defaults={
                    'citation': pr.citation,
                    'title': pr.title,
                    'source_urls': [SOURCE_URL],
                    'jurisdiction': 'NM',
                    'doc_type': 'policy',
                    'topic': topic,
                    'effective_from': None,
                    'effective_to': None,
                    'html_sha256': html_sha,
                },
            )

            # Replace sections for this version
            PolicySection.objects.filter(rule=rule_obj, version_tag=version_tag).delete()
            batch: List[PolicySection] = []
            for s in pr.sections:
                batch.append(
                    PolicySection(
                        rule=rule_obj,
                        version_tag=version_tag,
                        path=s.path,
                        heading=s.heading,
                        text=s.text,
                        anchor=s.anchor,
                        order_idx=s.order_idx,
                    )
                )
            PolicySection.objects.bulk_create(batch, batch_size=500)
            self.stdout.write(f"  wrote {len(batch)} sections for {pr.rule_id}@{version_tag}")
