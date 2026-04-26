"""Markdown -> structured parser for the company_discoverer output.

Validates the LLM's emitted markdown against the schema in
`docs/superpowers/specs/2026-04-26-company-discoverer-design.md` §5.1
and produces a list of :class:`CompanyEntry` records suitable for the
JSON sidecar (§5.2).

Pure module: re, dataclasses. No filesystem access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

VALID_CLUSTERS: frozenset[str] = frozenset({"direct", "adjacency", "cross_industry"})
VALID_CHANNELS: frozenset[str] = frozenset({"greenhouse", "ashby", "lever", "workday", "in_house", "unknown"})

_CLUSTER_HEADING_RE = re.compile(
    r"^##\s+Cluster:\s+(?P<label>.+?)\s*$",
    re.MULTILINE,
)
# Row format:
#   - **Name** — channel=foo. Reasoning: ... Citations: [1], [2].
# Citations clause is OPTIONAL: rows may omit it entirely when the model
# cannot confirm a verifiable URL. When present, must be `Citations: [N]`
# (one or more bracketed indices, optionally comma-separated). When absent,
# the row ends after the reasoning sentence.
_ROW_RE = re.compile(
    r"^\s*-\s+\*\*(?P<name>[^*]+?)\*\*\s*[—-]\s*"
    r"channel=(?P<channel>[a-z_]+)\.\s*"
    r"Reasoning:\s*(?P<reasoning>.+?)"
    r"(?:\s*Citations:\s*(?P<cites>(?:\[\d+\],?\s*)+))?"
    r"\s*\.?\s*$",
    re.MULTILINE,
)
_CITE_INDEX_RE = re.compile(r"\[(\d+)\]")
_REFERENCES_HEADING_RE = re.compile(r"^##\s+References\s*$", re.MULTILINE)
_REF_LINE_RE = re.compile(r"^\s*\[(\d+)\]\s*(\S.*?)\s*$", re.MULTILINE)


_LABEL_TO_CLUSTER: dict[str, str] = {
    "direct domain match": "direct",
    "transferable-competency adjacency": "adjacency",
    "cross-industry application": "cross_industry",
}


@dataclass(frozen=True)
class CompanyEntry:
    name: str
    cluster: str
    channel: str
    reasoning: str
    citations: tuple[str, ...]


@dataclass(frozen=True)
class ParseResult:
    markdown_clean: str
    companies: list[CompanyEntry] = field(default_factory=list)


class DiscoveryParseError(ValueError):
    """Raised when the LLM output fails a validation gate."""


def _resolve_references(md: str) -> dict[int, str]:
    ref_match = _REFERENCES_HEADING_RE.search(md)
    if not ref_match:
        return {}
    tail = md[ref_match.end() :]
    return {int(i): url.strip() for i, url in _REF_LINE_RE.findall(tail)}


def _label_to_cluster(label: str) -> str | None:
    return _LABEL_TO_CLUSTER.get(label.strip().lower())


def parse_markdown(md_text: str) -> ParseResult:
    """Parse ``md_text`` into a :class:`ParseResult`.

    Raises :class:`DiscoveryParseError` if any validation gate fails
    (≥3 companies, ≥2 clusters, well-formed rows, resolvable citations).
    """
    refs = _resolve_references(md_text)
    cluster_headings = list(_CLUSTER_HEADING_RE.finditer(md_text))
    companies: list[CompanyEntry] = []
    seen_clusters: set[str] = set()

    for i, h in enumerate(cluster_headings):
        cluster = _label_to_cluster(h.group("label"))
        if cluster is None:
            continue
        section_start = h.end()
        section_end = cluster_headings[i + 1].start() if i + 1 < len(cluster_headings) else len(md_text)
        ref_match = _REFERENCES_HEADING_RE.search(md_text, section_start, section_end)
        if ref_match:
            section_end = ref_match.start()
        section = md_text[section_start:section_end]
        for row in _ROW_RE.finditer(section):
            name = row.group("name").strip()
            channel = row.group("channel").strip()
            reasoning = row.group("reasoning").strip().rstrip(".").strip()
            cites_raw = row.group("cites") or ""
            cite_indices = [int(m.group(1)) for m in _CITE_INDEX_RE.finditer(cites_raw)]
            citations = tuple(refs[i] for i in cite_indices if i in refs)
            if not name:
                raise DiscoveryParseError(f"company entry has empty name in cluster {cluster!r}")
            if channel not in VALID_CHANNELS:
                raise DiscoveryParseError(
                    f"company {name!r} has invalid channel {channel!r} (must be one of {sorted(VALID_CHANNELS)})"
                )
            if not reasoning:
                raise DiscoveryParseError(f"company {name!r} has empty reasoning")
            companies.append(
                CompanyEntry(
                    name=name,
                    cluster=cluster,
                    channel=channel,
                    reasoning=reasoning,
                    citations=citations,
                )
            )
            seen_clusters.add(cluster)

    # Detect malformed rows that didn't match the row regex but should have:
    # any cluster section that contains "channel=" but yielded no parsed rows.
    for i, h in enumerate(cluster_headings):
        cluster = _label_to_cluster(h.group("label"))
        if cluster is None:
            continue
        section_start = h.end()
        section_end = cluster_headings[i + 1].start() if i + 1 < len(cluster_headings) else len(md_text)
        ref_match = _REFERENCES_HEADING_RE.search(md_text, section_start, section_end)
        if ref_match:
            section_end = ref_match.start()
        section = md_text[section_start:section_end]
        # Find bullet rows the strict row regex missed (e.g., missing channel)
        bullet_lines = [ln for ln in section.splitlines() if ln.lstrip().startswith("- **")]
        for ln in bullet_lines:
            if not _ROW_RE.match(ln):
                # Try to extract a name for a useful error
                m = re.search(r"\*\*([^*]+)\*\*", ln)
                bad_name = m.group(1).strip() if m else "<unknown>"
                if "channel=" not in ln:
                    raise DiscoveryParseError(f"company {bad_name!r} is missing channel field in cluster {cluster!r}")
                raise DiscoveryParseError(f"company {bad_name!r} row is malformed in cluster {cluster!r}")

    if len(companies) < 3:
        raise DiscoveryParseError(f"validation: at least 3 companies required, got {len(companies)}")
    if len(seen_clusters) < 2:
        raise DiscoveryParseError(f"validation: at least 2 clusters required, got {sorted(seen_clusters)}")

    return ParseResult(markdown_clean=md_text.strip(), companies=companies)
