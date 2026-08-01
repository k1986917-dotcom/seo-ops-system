"""Deterministic consistency checks shared by sectional pipeline stages."""

from __future__ import annotations

import re
from typing import Any

_EXPECTED_COLOR_BY_WAVELENGTH = {
    445: "blue",
    450: "blue",
    455: "blue",
    515: "green",
    520: "green",
    525: "green",
    532: "green",
    635: "red",
    638: "red",
    650: "red",
    660: "red",
    808: "infrared",
    980: "infrared",
    1064: "infrared",
}
_KNOWN_WAVELENGTHS = "|".join(str(item) for item in sorted(_EXPECTED_COLOR_BY_WAVELENGTH))
_COLOR = r"(?:blue|green|red|infrared|ir)"
_DIRECT_WAVELENGTH_COLOR = re.compile(
    rf"\b(?P<wavelength>{_KNOWN_WAVELENGTHS})\s*nm\s+"
    rf"(?:(?:laser|beam|light)\s+)?(?P<color>{_COLOR})\b",
    re.IGNORECASE,
)
_DIRECT_WAVELENGTH_COLOR_AFTER = re.compile(
    rf"\b(?P<wavelength>{_KNOWN_WAVELENGTHS})\s*nm\s+"
    rf"(?P<color>{_COLOR})\s+(?:laser|beam|light)\b",
    re.IGNORECASE,
)
_COLOR_WAVELENGTH = re.compile(
    rf"\b(?P<color>{_COLOR})\s+"
    rf"(?:(?:laser|beam|light)\s+)?(?P<wavelength>{_KNOWN_WAVELENGTHS})\s*nm\b",
    re.IGNORECASE,
)

_UNSAFE_METADATA_COMPLIANCE = re.compile(
    r"\b(?:OSHA|FDA|EPA|FTC|CDC|NIOSH|"
    r"Occupational\s+Safety\s+and\s+Health\s+Administration|"
    r"compliant|compliance|approved|certified)\b",
    re.IGNORECASE,
)
_UNSAFE_METADATA_SUPERLATIVE = re.compile(
    r"\b(?:best|safest|only\s+choice|go-to\s+choice|ultimate)\b",
    re.IGNORECASE,
)


def wavelength_color_conflicts(value: Any) -> list[dict[str, Any]]:
    """Return explicit wavelength/color contradictions found in text."""
    text = " ".join(str(value or "").split())
    if not text:
        return []
    found: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for pattern in (
        _DIRECT_WAVELENGTH_COLOR,
        _DIRECT_WAVELENGTH_COLOR_AFTER,
        _COLOR_WAVELENGTH,
    ):
        for match in pattern.finditer(text):
            wavelength = int(match.group("wavelength"))
            stated = match.group("color").casefold()
            if stated == "ir":
                stated = "infrared"
            expected = _EXPECTED_COLOR_BY_WAVELENGTH[wavelength]
            if stated == expected:
                continue
            identity = (wavelength, stated, expected)
            if identity in seen:
                continue
            seen.add(identity)
            found.append(
                {
                    "wavelength_nm": wavelength,
                    "stated_color": stated,
                    "expected_color": expected,
                }
            )
    return found


def contains_unsafe_metadata_compliance_claim(value: Any) -> bool:
    """Return whether user-visible metadata asserts unsupported compliance."""
    return bool(_UNSAFE_METADATA_COMPLIANCE.search(str(value or "")))


def contains_unsafe_legacy_metadata_claim(value: Any) -> bool:
    """Return whether inherited metadata contains compliance or superlatives."""
    text = str(value or "")
    return bool(
        _UNSAFE_METADATA_COMPLIANCE.search(text) or _UNSAFE_METADATA_SUPERLATIVE.search(text)
    )
