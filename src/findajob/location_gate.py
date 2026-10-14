"""Deterministic location gate for job scoring (#1217).

Runs inside :func:`findajob.scorer_prefilter.prefilter_score`, after the title
and employer hard rejects and before Stage 2. A posting whose location names a
place outside ``config/target_locations.txt`` is scored 2 with
``score_flag_reason = "location_mismatch"`` and never reaches the LLM.

The gate is a recognizer, not a matcher. It compares only what it can name with
confidence: a US state, a country, or a region such as ``EMEA``. City names are
never compared, so a posting in a target state but another city passes to the
scorer, whose role prompt caps it. A location the gate cannot classify
(``Dublin`` alone, ``See posting``, blank) passes too, because a Stage-1 reject
is never seen by the LLM or the operator. Every doubtful reading resolves to a
pass:

- A two-letter code is a US state only after a comma (``Austin, TX``), and only
  when the target list includes the US. ``LA`` alone, ``SF / LA`` and
  ``IN-OFFICE`` name no state. A code that is also a country code also counts
  as that country (``Berlin, DE``).
- A name with two meanings keeps both (``Washington`` is WA or DC).
- A work-mode word next to a place in one segment (``US Remote``,
  ``Remote in Canada``) does not hide the place.

Rules, in order — the first that holds lets the posting through:

1. Nothing in the location is recognized.
2. It names a world region (``Worldwide``, ``Global``, ``Anywhere``), or a
   region that covers the US (``Americas``, ``North America``) and the US is a
   target country.
3. A named US state is a target state.
4. A named non-US country is a target country.
5. The posting is in the US and the target list covers the whole US (a
   ``United States`` line), or the posting names the US without a state.
6. The posting is remote and in the US, and the US is a target country.
7. The posting names only a non-US region and the targets are not US-only.

So ``Remote - India`` is a mismatch for a US target list, and a bare ``Remote``
passes. A metro area that spans a state line needs one target line per state.

The gate is off when the file is absent, when ``FINDAJOB_LOCATION_GATE=off``,
when no line names a state or country (``Remote`` alone), or when any line is
not a form the gate can read (a city alone, a region, a province) — then it
logs ``location_gate_off`` once. The RapidAPI fetchers also read the file
(``fetchers/adapters/_locations.py``), so the switch lets an operator turn the
gate off without changing what they fetch.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from findajob.audit import log_event
from findajob.paths import BASE

_TARGET_LOCATIONS_PATH = Path(BASE) / "config" / "target_locations.txt"

_OFF_VALUES = frozenset({"off", "0", "false", "no"})

_US = "united states"

_STATE_NAMES: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "puerto rico": "PR",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_STATE_CODES: frozenset[str] = frozenset(_STATE_NAMES.values())

# Names that mean more than one place. Every meaning is kept, so a posting
# passes when any meaning is a target: a false pass costs one LLM call, a false
# reject hides the job.
_AMBIGUOUS_STATES: dict[str, frozenset[str]] = {"washington": frozenset({"WA", "DC"})}
_AMBIGUOUS_COUNTRIES: dict[str, str] = {"georgia": "georgia"}
# State codes that are also ISO country codes. Applied to postings only (in
# target_locations.txt, ``CA`` means California), and only as a hint: the
# country can make a posting match, never make it a mismatch, because
# ``Chennai, TN`` is in India, not Tunisia.
_CODE_ALSO_COUNTRY: dict[str, str] = {
    "AL": "albania",
    "AR": "argentina",
    "AZ": "azerbaijan",
    "CA": "canada",
    "CO": "colombia",
    "DE": "germany",
    "GA": "gabon",
    "ID": "indonesia",
    "IL": "israel",
    "IN": "india",
    "LA": "laos",
    "MA": "morocco",
    "MD": "moldova",
    "ME": "montenegro",
    "MN": "mongolia",
    "MT": "malta",
    "NE": "niger",
    "PA": "panama",
    "SC": "seychelles",
    "SD": "sudan",
    "TN": "tunisia",
}

_COUNTRY_ALIASES: dict[str, str] = {
    "us": _US,
    "usa": _US,
    "united states": _US,
    "united states of america": _US,
    "uk": "united kingdom",
    "gb": "united kingdom",
    "great britain": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "korea": "south korea",
    "republic of korea": "south korea",
    "czechia": "czech republic",
    "the netherlands": "netherlands",
    "holland": "netherlands",
    "uae": "united arab emirates",
    "côte d'ivoire": "ivory coast",
    "cote d'ivoire": "ivory coast",
    "türkiye": "turkey",
    "turkiye": "turkey",
    "the philippines": "philippines",
    "viet nam": "vietnam",
    "burma": "myanmar",
}

_COUNTRIES: frozenset[str] = frozenset(
    {
        "afghanistan", "albania", "algeria", "andorra", "angola", "argentina", "armenia",
        "australia", "austria", "azerbaijan", "bahamas", "bahrain", "bangladesh", "barbados",
        "belarus", "belgium", "belize", "benin", "bhutan", "bolivia", "bosnia and herzegovina",
        "botswana", "brazil", "brunei", "bulgaria", "burkina faso", "burundi", "cambodia",
        "cameroon", "canada", "cape verde", "chad", "chile", "china", "colombia", "costa rica",
        "croatia", "cuba", "cyprus", "czech republic", "denmark", "djibouti",
        "dominican republic", "ecuador", "egypt", "el salvador", "estonia", "eswatini",
        "ethiopia", "fiji", "finland", "france", "gabon", "gambia", "georgia", "germany",
        "ghana", "greece", "guatemala", "guinea", "guyana", "haiti", "honduras", "hong kong",
        "hungary", "iceland", "india", "indonesia", "iran", "iraq", "ireland", "israel",
        "italy", "ivory coast", "jamaica", "japan", "jordan", "kazakhstan", "kenya", "kosovo",
        "kuwait", "kyrgyzstan", "laos", "latvia", "lebanon", "lesotho", "liberia", "libya",
        "liechtenstein", "lithuania", "luxembourg", "macau", "madagascar", "malawi",
        "malaysia", "maldives", "mali", "malta", "mauritania", "mauritius", "mexico",
        "moldova", "monaco", "mongolia", "montenegro", "morocco", "mozambique", "myanmar",
        "namibia", "nepal", "netherlands", "new zealand", "nicaragua", "niger", "nigeria",
        "north korea", "north macedonia", "norway", "oman", "pakistan", "panama",
        "papua new guinea", "paraguay", "peru", "philippines", "poland", "portugal", "qatar",
        "romania", "russia", "rwanda", "saudi arabia", "senegal", "serbia", "seychelles", "sierra leone",
        "singapore", "slovakia", "slovenia", "somalia", "south africa", "south korea",
        "spain", "sri lanka", "sudan", "suriname", "sweden", "switzerland", "syria", "taiwan",
        "tajikistan", "tanzania", "thailand", "togo", "trinidad and tobago", "tunisia",
        "turkey", "turkmenistan", "uganda", "ukraine", "united arab emirates",
        "united kingdom", "uruguay", "uzbekistan", "venezuela", "vietnam", "yemen", "zambia",
        "zimbabwe",
    }
)  # fmt: skip

# Regions that contain no part of the US. A posting that names only one of
# these is a mismatch for a US-only target list; for any other target list it
# passes, because the gate does not map countries to regions.
_NON_US_REGIONS: frozenset[str] = frozenset(
    {
        "africa", "anz", "apac", "asia", "asia pacific", "benelux", "dach", "eea", "emea",
        "eu", "europe", "european union", "latam", "latin america", "mena", "middle east",
        "nordics", "oceania", "south america", "southeast asia",
    }
)  # fmt: skip
# Regions that include the US, and regions that include everywhere.
_US_REGIONS: frozenset[str] = frozenset({"americas", "the americas", "amer", "namer", "noram", "north america"})
_WORLD_REGIONS: frozenset[str] = frozenset({"worldwide", "global", "anywhere"})

# Separators between the parts of a location, captured so a code can be read
# as a state only when a comma comes before it. " or " between words separates
# alternatives in any case; "Salem, OR" keeps Oregon, because a comma, not a
# space, comes before it.
_SPLIT_RE = re.compile(r"(\s+or\s+|[,;:|/()\[\]·•\n\-–—])", re.IGNORECASE)
# "IN-OFFICE" and "On-site" would otherwise split into a bare "IN" or "On".
_WORK_MODE_COMPOUND_RE = re.compile(r"\b(?:in|on)[\s\-–—]+(?:office|site)\b", re.IGNORECASE)
_CODE_RE = re.compile(r"^(?P<code>[A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?$")
_REMOTE_RE = re.compile(r"\b(?:remote|anywhere|work from home|wfh|telecommut\w*)\b", re.IGNORECASE)
# Work-mode and filler words around a place in one segment: "US Remote",
# "Remote in the US". Removed only when the whole segment is not a place.
_WORK_MODE_RE = re.compile(
    r"\b(?:remote|work from home|wfh|telecommut\w*|hybrid|onsite|office"
    r"|in|the|from|only|based|within|national|nationwide)\b",
    re.IGNORECASE,
)
_WORLD_WORD_RE = re.compile(r"\b(?:anywhere|worldwide|global)\b", re.IGNORECASE)


@dataclass(frozen=True)
class LocationTargets:
    """What ``target_locations.txt`` allows, as the gate compares it."""

    states: frozenset[str]
    countries: frozenset[str]
    us_wide: bool
    lines: tuple[str, ...]


@dataclass(frozen=True)
class _Place:
    label: str
    states: frozenset[str]
    countries: frozenset[str]
    region: bool
    covers_us: bool = False
    covers_all: bool = False
    hint_countries: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _Segment:
    text: str
    after_comma: bool


def _segments(location: str) -> list[_Segment]:
    parts = _SPLIT_RE.split(_WORK_MODE_COMPOUND_RE.sub(" onsite ", location or ""))
    segments: list[_Segment] = []
    separator = ""
    for i, part in enumerate(parts):
        if i % 2:
            separator = part
            continue
        text = re.sub(r"\s+", " ", part.replace(".", "")).strip()
        if text:
            segments.append(_Segment(text=text, after_comma=separator == ","))
    return segments


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _classify(segment: _Segment, *, posting: bool, codes_are_states: bool) -> _Place | None:
    """Name the place one segment refers to, or None when it is not recognized."""
    without_work_mode = _squash(_WORK_MODE_RE.sub(" ", segment.text))
    for text in (segment.text, without_work_mode, _squash(_WORLD_WORD_RE.sub(" ", without_work_mode))):
        if text:
            place = _classify_exact(text, segment.after_comma, posting=posting, codes_are_states=codes_are_states)
            if place is not None:
                return place
    return None


def _classify_exact(text: str, after_comma: bool, *, posting: bool, codes_are_states: bool) -> _Place | None:
    states: set[str] = set()
    countries: set[str] = set()
    hint_countries: set[str] = set()
    m = _CODE_RE.match(text) if after_comma else None
    if m is not None and m.group("code") in _STATE_CODES:
        code = m.group("code")
        if codes_are_states:
            states.add(code)
        if posting and code in _CODE_ALSO_COUNTRY:
            hint_countries.add(_CODE_ALSO_COUNTRY[code])
    key = text.lower()
    if key in _AMBIGUOUS_STATES:
        states |= _AMBIGUOUS_STATES[key]
    elif key in _STATE_NAMES:
        states.add(_STATE_NAMES[key])
    if key in _AMBIGUOUS_COUNTRIES:
        countries.add(_AMBIGUOUS_COUNTRIES[key])
    elif key in _COUNTRY_ALIASES:
        countries.add(_COUNTRY_ALIASES[key])
    elif key in _COUNTRIES and key not in _STATE_NAMES:
        countries.add(key)
    region = key in _NON_US_REGIONS
    covers_us = key in _US_REGIONS
    covers_all = key in _WORLD_REGIONS
    if not (states or countries or region or covers_us or covers_all or hint_countries):
        return None
    return _Place(
        label=text,
        states=frozenset(states),
        countries=frozenset(countries),
        region=region,
        covers_us=covers_us,
        covers_all=covers_all,
        hint_countries=frozenset(hint_countries),
    )


def _read_targets(lines: list[str]) -> tuple[LocationTargets | None, list[str]]:
    """Build the gate from target lines; also return the lines it cannot read.

    A line counts when it names a US state or a country. A line of work-mode
    words only (``Remote``, ``Hybrid``) is skipped. Any other line — a city
    alone, a region, a province, ``Anywhere`` — means the gate cannot know the
    whole target area, so the gate is off.
    """
    states: set[str] = set()
    countries: set[str] = set()
    us_wide = False
    unreadable: list[str] = []
    for line in lines:
        segments = _segments(line)
        places = [p for seg in segments if (p := _classify(seg, posting=False, codes_are_states=True))]
        line_states = {s for p in places for s in p.states}
        line_countries = {c for p in places for c in p.countries}
        if not line_states and not line_countries:
            if any(_squash(_WORK_MODE_RE.sub(" ", seg.text)) for seg in segments):
                unreadable.append(line)
            continue
        if line_states:
            line_countries.add(_US)
        elif _US in line_countries:
            us_wide = True
        states |= line_states
        countries |= line_countries
    if unreadable or (not states and not countries):
        return None, unreadable
    targets = LocationTargets(
        states=frozenset(states),
        countries=frozenset(countries),
        us_wide=us_wide,
        lines=tuple(lines),
    )
    return targets, unreadable


def parse_targets(lines: list[str]) -> LocationTargets | None:
    """Build the gate from target lines; None when the gate is off for them."""
    return _read_targets(lines)[0]


def target_lines(path: Path | None = None) -> list[str]:
    """Non-blank, non-comment lines of the target file; [] when it is absent."""
    target = path or _TARGET_LOCATIONS_PATH
    if not target.exists():
        return []
    lines = (raw.strip() for raw in target.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


# Unreadable target lines already reported by this process, so triage logs
# one event, not one per scored job.
_warned: set[str] = set()


def load_targets(path: Path | None = None) -> LocationTargets | None:
    """The gate for this instance, or None when it is off."""
    if os.environ.get("FINDAJOB_LOCATION_GATE", "").strip().lower() in _OFF_VALUES:
        return None
    targets, unreadable = _read_targets(target_lines(path))
    key = "\n".join(unreadable)
    if unreadable and key not in _warned:
        _warned.add(key)
        log_event("location_gate_off", reason="unreadable_lines", lines=unreadable)
    return targets


def location_mismatch(location: str, targets: LocationTargets) -> str | None:
    """Return the place that puts the posting outside the targets, or None to pass."""
    us_target = _US in targets.countries
    segments = _segments(location)
    places = [p for seg in segments if (p := _classify(seg, posting=True, codes_are_states=us_target))]
    if any(p.covers_all or (p.covers_us and us_target) for p in places):
        return None
    if {c for p in places for c in p.hint_countries} & targets.countries:
        return None
    places = [p for p in places if p.states or p.countries or p.region]
    if not places:
        return None
    remote = any(_REMOTE_RE.search(seg.text) for seg in segments)
    states = {s for p in places for s in p.states}
    countries = {c for p in places for c in p.countries}
    in_us = bool(states) or _US in countries

    if states & targets.states:
        return None
    if (countries - {_US}) & targets.countries:
        return None
    if in_us and us_target and (targets.us_wide or not states):
        return None
    if remote and in_us and us_target:
        return None
    if not states and not countries and not targets.countries <= {_US}:
        return None

    def _inside(p: _Place) -> bool:
        return bool(
            p.states & targets.states
            or (p.countries - {_US}) & targets.countries
            or (_US in p.countries and not p.states and us_target)
        )

    return next((p.label for p in places if not _inside(p)), places[0].label)
