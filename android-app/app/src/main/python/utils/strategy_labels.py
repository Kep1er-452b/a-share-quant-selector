"""
Shared labels and filtering helpers for strategy signal categories.
"""

CATEGORY_LABELS = {
    "bowl_center": "🥣 回落碗中",
    "near_duokong": "📊 靠近多空线",
    "near_short_trend": "📈 靠近短期趋势线",
    "b1_v242b": "🎯 B1(V2.42B)",
    "b1_v242p": "🎯 B1(V2.42P)",
    "b1_v24261": "🎯 B1(V2.42.61)",
    "b2_beta": "🚀 B2选股Beta版",
    "b1_min_j_simple": "🔻 B1MinJSimple",
    "b1_min_j_complex": "🧩 B1MinJComplex",
}

CATEGORY_DISPLAY_ORDER = [
    "bowl_center",
    "near_duokong",
    "near_short_trend",
    "b1_v242b",
    "b1_v242p",
    "b1_v24261",
    "b2_beta",
    "b1_min_j_simple",
    "b1_min_j_complex",
]

INVALID_STOCK_NAME_KEYWORDS = ("退", "未知", "退市", "已退")


STRATEGY_GROUPS = [
    {
        "key": "b1",
        "label": "B1",
        "description": "B1 系列底部与量价策略",
        "order": 10,
    },
    {
        "key": "b2",
        "label": "B2",
        "description": "B2 系列确认与启动策略",
        "order": 20,
    },
    {
        "key": "bowl",
        "label": "Bowl",
        "description": "碗口反弹与趋势回踩策略",
        "order": 30,
    },
    {
        "key": "other",
        "label": "Other",
        "description": "其他独立策略",
        "order": 90,
    },
]


STRATEGY_UI_METADATA = {
    "B1V242BStrategy": {
        "group": "b1",
        "label": "242B",
        "description": "B1 V2.42B",
        "order": 10,
    },
    "B1V242PStrategy": {
        "group": "b1",
        "label": "242P",
        "description": "B1 V2.42P",
        "order": 20,
    },
    "B1V24261Strategy": {
        "group": "b1",
        "label": "V2.42.61",
        "description": "2026 年 6 月第 1 周公式",
        "order": 30,
    },
    "B1MinJSimpleStrategy": {
        "group": "b1",
        "label": "Min J Simple",
        "description": "动态 Min J 简化版",
        "order": 40,
    },
    "B1MinJComplexStrategy": {
        "group": "b1",
        "label": "Min J Complex",
        "description": "动态 Min J 完整版",
        "order": 50,
    },
    "B2BetaStrategy": {
        "group": "b2",
        "label": "Beta",
        "description": "B2 选股 Beta 版",
        "order": 10,
    },
    "BowlReboundStrategy": {
        "group": "bowl",
        "label": "Rebound",
        "description": "碗口反弹策略",
        "order": 10,
    },
}


def strategy_ui_metadata(strategy_name):
    """Return stable grouping and display metadata for the Selection page."""
    metadata = dict(STRATEGY_UI_METADATA.get(strategy_name, {}))
    metadata.setdefault("group", "other")
    metadata.setdefault("label", strategy_name)
    metadata.setdefault("description", strategy_name)
    metadata.setdefault("order", 999)
    return metadata


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
