"""Canonical identities for mainland listed equities.

The application stores A-share CSVs by the six digit code, while provider and
cross-market APIs use ``CODE.VENUE`` symbols.  Keeping this mapping in one
small dependency-free module prevents the same security from being routed to
different exchanges by different surfaces.
"""

from __future__ import annotations


_SUPPORTED_SUFFIXES = frozenset({"SH", "SZ", "BJ"})


def a_share_code(value: object) -> str:
    """Return a validated six-digit A-share code without a venue suffix."""

    text = str(value or "").strip().upper()
    code = text.split(".", 1)[0]
    if len(code) != 6 or not code.isdigit():
        raise ValueError("A-share symbol must contain six digits")
    return code


def a_share_suffix(code: object) -> str:
    """Return the venue implied by the current A-share code ranges."""

    normalized = a_share_code(code)
    if normalized.startswith(("4", "8", "92")):
        return "BJ"
    if normalized.startswith(("60", "68")):
        return "SH"
    if normalized.startswith(("00", "30")):
        return "SZ"
    # Preserve the historical project fallback for other six-digit A-share
    # codes, while still rejecting an explicitly contradictory suffix.
    return "SZ"


def canonical_a_share_symbol(value: object) -> str:
    """Return a canonical ``CODE.VENUE`` identity.

    An explicit suffix is evidence supplied by the caller.  It is never
    silently rewritten to another exchange when it conflicts with the code
    range; callers must correct the source identity instead.
    """

    text = str(value or "").strip().upper()
    code = a_share_code(text)
    expected = a_share_suffix(code)
    if "." in text:
        suffix = text.rsplit(".", 1)[1].strip()
        if suffix not in _SUPPORTED_SUFFIXES:
            raise ValueError(f"unsupported A-share venue suffix: {suffix}")
        if suffix != expected:
            raise ValueError(
                f"A-share symbol {text} conflicts with code-implied venue {expected}"
            )
    return f"{code}.{expected}"


__all__ = ["a_share_code", "a_share_suffix", "canonical_a_share_symbol"]
