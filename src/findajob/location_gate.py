"""Deterministic location gate for job scoring (#1217).

Runs inside :func:`findajob.scorer_prefilter.prefilter_score`, after the title
and employer hard rejects and before Stage 2. A posting whose location names a
place outside ``config/target_locations.txt`` is scored 2 with
``score_flag_reason = "location_mismatch"`` and never reaches the LLM.

The gate is a recognizer, not a matcher. It compares only what it can name with
confidence: a US state (code or full name), a country, or a non-US region such
as ``EMEA``. City names are never compared, so a posting in a target state but
another city passes to the scorer, whose role prompt caps it. A location the
gate cannot classify (``Dublin`` alone, ``See posting``, blank) passes too,
because a Stage-1 reject is never seen by the LLM or the operator.

Rules, in order — the first that holds lets the posting through:

1. Nothing in the location is recognized.
2. A named US state is a target state.
3. A named non-US country is a target country.
4. The posting is in the US and the target list covers the whole US (a
   ``United States`` line), or the posting names the US without a state.
5. The posting is remote and in the US, and the US is a target country.
6. The posting names only a non-US region and the targets are not US-only.

So ``Remote - India`` is a mismatch for a US target list, and a bare ``Remote``
passes. A work-mode word next to a place in one segment (``US Remote``,
``Remote in Canada``) does not hide the place. A metro area that spans a state
line needs one target line per state.

The gate is off when the file is absent, when no line names a recognized place
(``Remote`` alone), or when ``FINDAJOB_LOCATION_GATE=off``. The RapidAPI
fetchers also read the file (``fetchers/adapters/_locations.py``), so the
switch lets an operator turn the gate off without changing what they fetch.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

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
# Two-letter codes that feeds also use for a country. Applied to postings only:
# in target_locations.txt, ``CA`` means California.
_CODE_ALSO_COUNTRY: dict[str, str] = {"CA": "canada"}

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
        "romania", "russia", "rwanda", "saudi arabia", "senegal", "serbia", "sierra leone",
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

# Separators between the parts of a location. " or " between words separates
# alternatives in any case; "Salem, OR" keeps Oregon, because a comma, not a
# space, comes before it.
_SPLIT_RE = re.compile(r"[,;|/()\[\]·•\n\-–—]|\s+or\s+", re.IGNORECASE)
_CODE_RE = re.compile(r"^(?P<code>[A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?$")
_TRAILING_CODE_RE = re.compile(r"\s(?P<code>[A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?$")
_REMOTE_RE = re.compile(r"\b(?:remote|anywhere|work from home|wfh|telecommut\w*)\b", re.IGNORECASE)
# Work-mode and filler words around a place in one segment: "US Remote",
# "Remote in the US". Removed only when the whole segment is not a place.
_WORK_MODE_RE = re.compile(
    r"\b(?:remote|anywhere|work from home|wfh|telecommut\w*|hybrid|onsite|in office|office"
    r"|in|the|from|only|based|within|national|nationwide)\b",
    re.IGNORECASE,
)


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


def _segments(location: str) -> list[str]:
    parts = (re.sub(r"\s+", " ", p.replace(".", "")).strip() for p in _SPLIT_RE.split(location or ""))
    return [p for p in parts if p]


def _classify(segment: str, *, posting: bool) -> _Place | None:
    """Name the place one segment refers to, or None when it is not recognized."""
    place = _classify_exact(segment, posting=posting)
    if place is not None:
        return place
    core = re.sub(r"\s+", " ", _WORK_MODE_RE.sub(" ", segment)).strip()
    if core and core != segment:
        place = _classify_exact(core, posting=posting)
    return place


def _classify_exact(segment: str, *, posting: bool) -> _Place | None:
    states: set[str] = set()
    countries: set[str] = set()
    m = _CODE_RE.match(segment)
    if m is None and any(c.islower() for c in segment):
        m = _TRAILING_CODE_RE.search(segment)
    if m is not None and m.group("code") in _STATE_CODES:
        code = m.group("code")
        states.add(code)
        if posting and code in _CODE_ALSO_COUNTRY:
            countries.add(_CODE_ALSO_COUNTRY[code])
    key = segment.lower()
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
    if not states and not countries and not region:
        return None
    return _Place(label=segment, states=frozenset(states), countries=frozenset(countries), region=region)


def parse_targets(lines: list[str]) -> LocationTargets | None:
    """Build the gate from target lines; None when no line names a recognized place."""
    states: set[str] = set()
    countries: set[str] = set()
    us_wide = False
    for line in lines:
        places = [p for seg in _segments(line) if (p := _classify(seg, posting=False))]
        line_states = {s for p in places for s in p.states}
        line_countries = {c for p in places for c in p.countries}
        if line_states:
            line_countries.add(_US)
        elif _US in line_countries:
            us_wide = True
        states |= line_states
        countries |= line_countries
    if not states and not countries:
        return None
    return LocationTargets(
        states=frozenset(states),
        countries=frozenset(countries),
        us_wide=us_wide,
        lines=tuple(lines),
    )


def target_lines(path: Path | None = None) -> list[str]:
    """Non-blank, non-comment lines of the target file; [] when it is absent."""
    target = path or _TARGET_LOCATIONS_PATH
    if not target.exists():
        return []
    lines = (raw.strip() for raw in target.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def load_targets(path: Path | None = None) -> LocationTargets | None:
    """The gate for this instance, or None when it is off."""
    if os.environ.get("FINDAJOB_LOCATION_GATE", "").strip().lower() in _OFF_VALUES:
        return None
    return parse_targets(target_lines(path))


def location_mismatch(location: str, targets: LocationTargets) -> str | None:
    """Return the place that puts the posting outside the targets, or None to pass."""
    segments = _segments(location)
    places = [p for seg in segments if (p := _classify(seg, posting=True))]
    if not places:
        return None
    remote = any(_REMOTE_RE.search(seg) for seg in segments)
    states = {s for p in places for s in p.states}
    countries = {c for p in places for c in p.countries}
    in_us = bool(states) or _US in countries
    us_target = _US in targets.countries

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
