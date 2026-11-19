"""Explicit, non-identifying raw signals and tentative developer definitions."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from types import MappingProxyType


@dataclass(frozen=True)
class DebugField:
    group: str
    field: str
    meaning_zh: str
    meaning_status: str
    unit_status: str
    enum_status: str
    evidence: str
    source: str
    unit_hint: str | None = None
    scale_hint: str | None = None
    known_codes: dict | None = None
    evidence_note: str | None = None

    @property
    def path(self):
        return self.group + "." + self.field


def _catalog():
    document = json.loads(Path(__file__).with_name("debug_fields.json").read_text(encoding="utf-8"))
    if document["schema_version"] != 1:
        raise ValueError("Unsupported debug field definitions")
    fields = [DebugField(**item) for item in document["fields"]]
    if (len({item.path for item in fields}) != len(fields)
            or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*", item.path) for item in fields)):
        raise ValueError("Invalid debug field definitions")
    return MappingProxyType({item.path: item for item in fields})


DEBUG_FIELDS = _catalog()


def bounded_debug_value(value):
    """Return a scalar state and validation status, without guesses or scaling.

    HA sensor states are limited to 255 characters. Flat scalar lists (such as
    weekday selections) use JSON. Nested responses and arbitrary objects are
    never copied into attributes or state.
    """
    if type(value) in (int, float):
        try:
            if not math.isfinite(value):
                return None, "non_finite"
        except OverflowError:
            return None, "out_of_range"
        if len(str(value)) > 255:
            return None, "too_long"
        return value, "valid"
    if type(value) is bool:
        return "true" if value else "false", "valid"
    if isinstance(value, str):
        if len(value) > 255:
            return None, "too_long"
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            return None, "control_characters"
        return value, "valid"
    if isinstance(value, list):
        if len(value) > 64 or any(type(v) not in (int, float, bool, str, type(None)) for v in value):
            return None, "unsupported_type"
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (ValueError, OverflowError):
            return None, "non_finite"
        if len(serialized) > 255:
            return None, "too_long"
        return serialized, "json_array"
    return None, "unsupported_type"
