import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

import requests
from bs4 import BeautifulSoup  # type: ignore
from django.core.management.base import BaseCommand, CommandParser


BASE_URL = "https://www.law.cornell.edu/regulations/texas/title-16/part-1/chapter-3"


@dataclass
class Section:
    path: str
    heading: str
    text: str
    anchor: str
    order_idx: int


def fetch_html(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_chapter_index(html: str) -> List[Tuple[str, str]]:
    """Return list of (rule_id, url) for §3.x pages in Chapter 3.

    More robust: match the section number from link text like "§ 3.14"; fallback to URL patterns.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = "https://www.law.cornell.edu"
    found_pairs: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    anchors = soup.find_all("a")
    for a in anchors:
        text = (a.get_text(" ", strip=True) or "")
        href = a.get("href")
        if not href:
            continue
        # Prefer extracting the section number from link text like "§ 3.14"
        m = re.search(r"§\s*3\.(\d+)", text)
        num = m.group(1) if m else None
        if not num:
            # Fallback to href patterns that include the section number
            m2 = re.search(r"(?:/|-)3\.(\d+)", href)
            if m2:
                num = m2.group(1)
        if not num:
            continue
        rule_id = f"tx.tac.16.3.{num}"
        abs_url = requests.compat.urljoin(base, href)
        key = (rule_id, abs_url)
        if key in seen:
            continue
        found_pairs.append(key)
        seen.add(key)

    return found_pairs


def parse_rule_sections(html: str) -> Iterable[Section]:
    """Parse a rule page into ordered legal subsections using regex-based nesting.

    Strategy:
    - Extract textual blocks from likely content containers.
    - Detect subsection markers at line starts: (a), (1), (A), etc.
    - Maintain a nesting stack to build paths like a → a(1) → a(1)(A).
    - Skip site boilerplate (Notes, Toolbox).
    """
    soup = BeautifulSoup(html, "html.parser")
    # candidate containers in order of preference
    candidates = [
        soup.select_one("article"),
        soup.select_one("main"),
        soup.select_one("#content"),
        soup.select_one(".content"),
        soup,
    ]
    root = next((c for c in candidates if c), soup)

    # Collect paragraph-like nodes
    nodes = root.find_all(["p", "li", "div", "section"], recursive=True)
    # patterns: (a) (1) (A)
    pat = re.compile(r"^\((?P<tok>[a-z]|[0-9]+|[A-Z])\)\s*")

    sections: List[Section] = []
    stack: List[str] = []
    buf_text: List[str] = []

    def flush(order_idx: int):
        if not stack:
            return
        path = stack[0]
        for tok in stack[1:]:
            path += f"({tok})"
        text = "\n".join(buf_text).strip()
        if not text:
            return
        sections.append(Section(path=path, heading="", text=text, anchor="", order_idx=order_idx))

    order = 0
    for node in nodes:
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        # Skip boilerplate
        low = text.lower()
        if low.startswith("notes") or "state regulations toolbox" in low:
            continue

        m = pat.match(text)
        if m:
            # new subsection token found
            tok = m.group("tok")
            # Determine nesting level by token type
            # level 0: letters a-z, level 1: digits, level 2: letters A-Z
            if tok.isalpha() and tok.islower():
                level = 0
            elif tok.isdigit():
                level = 1
            else:
                level = 2

            # Flush previous when changing same-or-higher level
            if stack:
                # trim stack to this level
                while len(stack) > level + 1:
                    stack.pop()
                # If same level, flush current buffer
                if len(stack) == level + 1:
                    flush(order)
                    order += 1
                    buf_text = []  # type: ignore

            # Ensure stack length
            while len(stack) < level + 1:
                stack.append("")
            stack[level] = tok
            # reset deeper levels
            del stack[level + 1 :]

            # Append the remainder of text after the marker to buffer
            remainder = text[m.end() :].strip()
            if remainder:
                buf_text.append(remainder)
            continue

        # Continuation of current subsection text
        if stack:
            buf_text.append(text)
        else:
            # No subsection marker yet; treat as preamble
            continue

    # Flush tail
    flush(order)

    return sections


class Command(BaseCommand):
    help = "Fetch and parse Texas TAC Chapter 3 from Cornell; print summary or upsert when --write is used."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--rule", dest="rule", help="Limit to a specific rule_id like tx.tac.16.3.14")
        parser.add_argument("--write", action="store_true", help="Persist to DB (PolicyRule/PolicySection)")
        parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="Print summaries even when --write is used")
        parser.add_argument("--version-tag", dest="version_tag", default="manual", help="Version tag to apply (e.g., 2025-Q4)")

    def handle(self, *args, **options):
        from apps.policy_ingest.models import PolicyRule, PolicySection

        version_tag: str = options["version_tag"]
        limit_rule: str | None = options.get("rule")
        do_write: bool = options.get("write", False)
        do_dry: bool = options.get("dry_run", False)

        index_html = fetch_html(BASE_URL)
        rules = parse_chapter_index(index_html)
        self.stdout.write(f"Discovered {len(rules)} Chapter 3 rule links from index {BASE_URL}")
        for rid, url in rules[:10]:
            self.stdout.write(f"  - {rid} -> {url}")
        if limit_rule:
            rules = [r for r in rules if r[0] == limit_rule]

        for rule_id, url in rules:
            page_html = fetch_html(url)
            html_sha = hashlib.sha256(page_html.encode("utf-8")).hexdigest()
            # Emit summary
            self.stdout.write(f"Fetched {rule_id} -> {url} sha={html_sha[:12]}")

            # Always show a small preview when dry-run requested or when not writing
            if do_dry or not do_write:
                secs = list(parse_rule_sections(page_html))[:5]
                for s in secs:
                    self.stdout.write(f"  - {s.order_idx:03d} {s.path} {s.heading} :: {s.text[:80]}")
                if not do_write:
                    continue

            # Infer tags for now (can be refined later per rule family)
            jurisdiction = 'TX'
            doc_type = 'policy'
            topic = 'plugging' if rule_id.endswith('3.14') else None

            rule_obj, _ = PolicyRule.objects.update_or_create(
                rule_id=rule_id,
                version_tag=version_tag,
                defaults={
                    'citation': rule_id.replace('tx.tac.', '').replace('.', ' '),
                    'title': '',
                    'source_urls': [url],
                    'jurisdiction': jurisdiction,
                    'doc_type': doc_type,
                    'topic': topic,
                    'effective_from': None,
                    'effective_to': None,
                    'html_sha256': html_sha,
                },
            )

            # Replace sections for this version
            PolicySection.objects.filter(rule=rule_obj, version_tag=version_tag).delete()
            batch: List[PolicySection] = []
            for s in parse_rule_sections(page_html):
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
            self.stdout.write(f"  wrote {len(batch)} sections for {rule_id}@{version_tag}")


