"""Loader for config/rapidapi_feeds.yaml (#408)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class CurationLoadError(Exception):
    """Raised when rapidapi_feeds.yaml is missing or malformed."""


@dataclass(frozen=True)
class AdapterMetadata:
    name: str
    display_name: str
    rapidapi_url: str = ""
    free_tier: str = ""
    paid_tier: str = ""
    required_env_var: str = ""
    best_for: str = ""
    worst_for: str = ""


@dataclass(frozen=True)
class CandidateClass:
    name: str
    description: str
    recommended_adapter: str  # adapter name
    rationale: str


@dataclass(frozen=True)
class Curation:
    default_name: str
    classes: list[CandidateClass] = field(default_factory=list)
    adapters: list[AdapterMetadata] = field(default_factory=list)

    def adapter_by_name(self, name: str) -> AdapterMetadata | None:
        return next((a for a in self.adapters if a.name == name), None)


def load_curation(path: Path) -> Curation:
    if not path.exists():
        raise CurationLoadError(f"Curation file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise CurationLoadError(f"Malformed YAML in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise CurationLoadError(f"Top-level YAML must be a mapping in {path}")
    if "default" not in raw:
        raise CurationLoadError(f"Missing required 'default' field in {path}")

    adapters = [
        AdapterMetadata(
            name=a["name"],
            display_name=a.get("display_name", a["name"]),
            rapidapi_url=a.get("rapidapi_url", ""),
            free_tier=a.get("free_tier", ""),
            paid_tier=a.get("paid_tier", ""),
            required_env_var=a.get("required_env_var", ""),
            best_for=(a.get("coverage") or {}).get("best_for", ""),
            worst_for=(a.get("coverage") or {}).get("worst_for", ""),
        )
        for a in raw.get("adapters", []) or []
    ]
    adapter_names = {a.name for a in adapters}

    default_name = raw["default"]
    if default_name not in adapter_names:
        raise CurationLoadError(f"default '{default_name}' not in adapters list ({adapter_names}) in {path}")

    classes = []
    for c in raw.get("classes", []) or []:
        if c["recommended_adapter"] not in adapter_names:
            raise CurationLoadError(
                f"class '{c['name']}' recommends '{c['recommended_adapter']}' which is not in adapters list"
            )
        classes.append(
            CandidateClass(
                name=c["name"],
                description=c.get("description", ""),
                recommended_adapter=c["recommended_adapter"],
                rationale=c.get("rationale", ""),
            )
        )

    return Curation(default_name=default_name, classes=classes, adapters=adapters)


def recommend_for_class(curation: Curation, class_name: str) -> AdapterMetadata:
    """Return the recommended adapter metadata for a class, falling back to default."""
    match = next((c for c in curation.classes if c.name == class_name), None)
    if match is None:
        return default_adapter(curation)
    adapter = curation.adapter_by_name(match.recommended_adapter)
    if adapter is None:
        # Should be impossible after load_curation validates, but defensive
        return default_adapter(curation)
    return adapter


def default_adapter(curation: Curation) -> AdapterMetadata:
    adapter = curation.adapter_by_name(curation.default_name)
    if adapter is None:
        raise CurationLoadError(f"Default adapter '{curation.default_name}' not found")
    return adapter
