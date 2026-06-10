from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.market_overview import _build_market_stats, _group_stocks_by_industry


def _stock(code, name, board, change_pct, previous_close=10.0, latest_price=None, data_count=100):
    return {
        "code": code,
        "name": name,
        "board": board,
        "latest_date": "2026-06-10",
        "latest_price": latest_price if latest_price is not None else previous_close * (1 + change_pct / 100),
        "previous_close": previous_close,
        "market_cap": 1_000_000_000,
        "data_count": data_count,
        "metrics": {"daily": change_pct},
    }


def test_market_stats_use_board_and_st_rules_for_price_limits():
    stocks = [
        _stock("600001", "主板普通", "main", 10.0, latest_price=11.0),
        _stock("600002", "*ST测试", "main", 5.0, latest_price=10.5),
        _stock("300001", "创业测试", "chinext", 20.0, latest_price=12.0),
        _stock("688001", "科创测试", "star", -20.0, latest_price=8.0),
        _stock("600003", "主板跌停", "main", -10.0, latest_price=9.0),
        _stock("301531", "上市第三日", "chinext", 20.0, latest_price=12.0, data_count=3),
    ]

    stats = _build_market_stats(stocks, "daily")

    assert stats["limit_up_count"] == 3
    assert stats["limit_down_count"] == 2
    assert stats["distribution"][0]["count"] == 3
    assert stats["distribution"][-1]["count"] == 2


def test_market_stats_round_limit_prices_to_the_price_tick():
    stocks = [
        _stock("600010", "价格取整", "main", 9.96, previous_close=5.02, latest_price=5.52),
        _stock("600011", "未到涨停", "main", 9.76, previous_close=5.02, latest_price=5.51),
    ]

    stats = _build_market_stats(stocks, "daily")

    assert stats["limit_up_count"] == 1


def test_industry_groups_include_breadth_and_median_fields():
    stocks = [
        _stock("600001", "甲", "main", 3.0),
        _stock("600002", "乙", "main", -1.0),
        _stock("600003", "丙", "main", 0.0),
    ]
    groups = _group_stocks_by_industry(
        stocks,
        {"600001": "测试行业", "600002": "测试行业", "600003": "测试行业"},
        "daily",
    )

    assert groups[0]["change_pct"] == 0.6667
    assert groups[0]["median_change_pct"] == 0.0
    assert groups[0]["up_count"] == 1
    assert groups[0]["down_count"] == 1
    assert groups[0]["flat_count"] == 1
