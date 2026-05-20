from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from wyckoff_ai.naming import build_wyckoff_output_paths, get_latest_data_date, safe_filename_part, to_pinyin_slug


def test_safe_filename_part():
    assert safe_filename_part(" 000001.SZ ") == "000001.sz"
    assert safe_filename_part("abc / DEF:?") == "abc-def"


def test_to_pinyin_slug_fallback_is_safe():
    slug = to_pinyin_slug("", "000001.SZ")
    assert slug == "000001.sz"


def test_latest_date_and_paths():
    df = pd.DataFrame({"date": ["2026-05-18", "2026-05-19"]})
    date = get_latest_data_date(df)
    paths = build_wyckoff_output_paths("000001", "平安银行", date)
    assert date == "2026-05-19"
    assert paths["chart_path"].endswith("2026-05-19-wyckoff-chart.png")
    assert "charts" in paths["chart_path"]
