"""
Shared labels and filtering helpers for strategy signal categories.
"""

CATEGORY_LABELS = {
    "bowl_center": "🥣 回落碗中",
    "near_duokong": "📊 靠近多空线",
    "near_short_trend": "📈 靠近短期趋势线",
    "b1_v242b": "🎯 B1(V2.42B)",
    "b2_beta": "🚀 B2选股Beta版",
    "b1_min_j_simple": "🔻 B1MinJSimple",
    "b1_min_j_complex": "🧩 B1MinJComplex",
}

CATEGORY_DISPLAY_ORDER = [
    "bowl_center",
    "near_duokong",
    "near_short_trend",
    "b1_v242b",
    "b2_beta",
    "b1_min_j_simple",
    "b1_min_j_complex",
]

INVALID_STOCK_NAME_KEYWORDS = ("退", "未知", "退市", "已退")


def category_label(category):
    """Return the user-facing label for a signal category."""
    return CATEGORY_LABELS.get(category, str(category or "unknown"))


def iter_categories_with_unknowns(category_counts=None, category_groups=None):
    """Yield known categories first, then any extra categories in stable order."""
    seen = set()
    for category in CATEGORY_DISPLAY_ORDER:
        seen.add(category)
        yield category

    extras = set()
    for source in (category_counts or {}, category_groups or {}):
        extras.update(source.keys())

    for category in sorted(item for item in extras if item not in seen):
        yield category


def is_invalid_stock_name(name, missing_name_is_invalid=True):
    """
    Filter delisted/ST/unknown names.

    When a name cache is missing, callers that still have a trusted code can set
    missing_name_is_invalid=False to avoid accidentally filtering the whole pool.
    """
    text = str(name or "").strip()
    if not text:
        return bool(missing_name_is_invalid)
    if not missing_name_is_invalid and text == "未知":
        return False
    if any(keyword in text for keyword in INVALID_STOCK_NAME_KEYWORDS):
        return True
    return text.startswith("ST") or text.startswith("*ST")


def fallback_stock_name(code):
    """Safe display fallback that should not be treated as a bad stock name."""
    return f"股票{str(code).zfill(6)}"
