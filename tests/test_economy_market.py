from __future__ import annotations

import pytest

from market_data.economy import EconomyService, economy_catalog
from market_data.models import SyncRequest
from market_data.store import DomainStore


@pytest.fixture
def service(tmp_path):
    store = DomainStore(tmp_path / "economy.sqlite")
    cpi_rows = []
    for offset in range(300):
        year = 2000 + offset // 12
        month = offset % 12 + 1
        cpi_rows.append(
            {
                "month": f"{year:04d}{month:02d}",
                "nt_val": 100.0 + offset / 100,
                "nt_yoy": -1.0 + offset / 50,
                "nt_mom": offset / 1000,
            }
        )
    store.upsert_rows(
        "cn_cpi",
        cpi_rows,
        key_fields=("month",),
        date_field="month",
    )
    store.upsert_rows(
        "cn_gdp",
        [
            {"quarter": "2025Q4", "gdp": 1_349_084.0, "gdp_yoy": 5.0},
            {"quarter": "2026Q1", "gdp": 335_000.0, "gdp_yoy": 5.2},
        ],
        key_fields=("quarter",),
        date_field="quarter",
    )
    store.upsert_rows(
        "cn_m",
        [
            {"month": "202605", "m2": 325_7800.0, "m2_yoy": 7.9},
            {"month": "202606", "m2": 328_1200.0, "m2_yoy": 8.1},
        ],
        key_fields=("month",),
        date_field="month",
    )
    store.upsert_rows(
        "cn_pmi",
        [
            {"month": "202605", "pmi010000": 49.8},
            {"month": "202606", "pmi010000": 50.1},
        ],
        key_fields=("month",),
        date_field="month",
    )
    store.upsert_rows(
        "shibor",
        [
            {"date": "20260709", "on": 1.314, "1w": 1.422},
            {"date": "20260710", "on": 1.307, "1w": 1.417},
        ],
        key_fields=("date",),
        date_field="date",
    )
    return EconomyService(store)


def test_economy_catalog_registers_verified_provider_datasets_and_keys():
    specs = {spec.dataset_id: spec for spec in economy_catalog()}

    assert set(specs) == {
        "cn_gdp",
        "cn_cpi",
        "cn_ppi",
        "cn_m",
        "cn_pmi",
        "shibor",
    }
    assert specs["cn_gdp"].key_fields == ("quarter",)
    assert specs["cn_gdp"].date_field == "quarter"
    assert specs["cn_cpi"].key_fields == ("month",)
    assert specs["shibor"].key_fields == ("date",)
    assert specs["cn_pmi"].required is False


def test_economy_full_plans_use_explicit_frequency_bounds(tmp_path):
    catalog = economy_catalog()
    store = DomainStore(tmp_path / "plan.sqlite")
    request = SyncRequest(domain="economy")

    gdp = tuple(catalog.get("cn_gdp").request_planner(request, None, store))
    cpi = tuple(catalog.get("cn_cpi").request_planner(request, None, store))
    shibor = tuple(catalog.get("shibor").request_planner(request, None, store))

    assert gdp and all(page.params.get("start_q") and page.params.get("end_q") for page in gdp)
    assert cpi and all(page.params.get("start_m") and page.params.get("end_m") for page in cpi)
    assert shibor and all(page.params.get("start_date") and page.params.get("end_date") for page in shibor)


def test_macro_series_always_declares_exact_field_unit_frequency_and_precision(service):
    items = service.series_catalog()
    by_id = {item["series_id"]: item for item in items}

    assert all(
        item["field"]
        and item["unit"]
        and item["frequency"]
        and item["family"]
        and isinstance(item["precision"], int)
        for item in items
    )
    assert by_id["cn_gdp.gdp"]["field"] == "gdp"
    assert by_id["cn_gdp.gdp"]["unit"] == "亿元"
    assert by_id["cn_cpi.nt_yoy"]["field"] == "nt_yoy"
    assert by_id["cn_ppi.ppi_yoy"]["frequency"] == "monthly"
    assert by_id["cn_m.m2"]["unit"] == "亿元"
    assert by_id["cn_m.m2_yoy"]["unit"] == "%"
    assert by_id["cn_pmi.headline"]["field"] == "pmi010000"
    assert by_id["shibor.on"]["frequency"] == "daily"
    assert by_id["shibor.on"]["precision"] == 4


def test_macro_series_catalog_filters_one_family_without_losing_metadata(service):
    items = service.series_catalog("inflation")

    assert items
    assert {item["family"] for item in items} == {"inflation"}
    assert {item["dataset"] for item in items} == {"cn_cpi", "cn_ppi"}


def test_macro_overview_downsamples_stably_but_detail_preserves_exact_rows(service):
    first = service.series(["cn_cpi.nt_yoy"], max_points=120)
    repeated = service.series(["cn_cpi.nt_yoy"], max_points=120)
    detail = service.series(["cn_cpi.nt_yoy"], max_points=2000)

    overview_points = first["series"][0]["points"]
    detail_points = detail["series"][0]["points"]
    assert len(overview_points) == 120
    assert len(detail_points) == 300
    assert overview_points == repeated["series"][0]["points"]
    assert overview_points[0] == detail_points[0]
    assert overview_points[-1] == detail_points[-1]
    assert first["series"][0]["sampled"] is True
    assert detail["series"][0]["sampled"] is False


def test_macro_series_compares_families_and_preserves_exact_stored_values(service):
    payload = service.series(
        ["cn_gdp.gdp_yoy", "cn_m.m2_yoy", "cn_pmi.headline", "shibor.on"],
        start="202601",
        end="20260710",
        max_points=2000,
    )
    by_id = {item["series_id"]: item for item in payload["series"]}

    assert by_id["cn_gdp.gdp_yoy"]["points"] == [
        {"period": "2026Q1", "value": 5.2}
    ]
    assert by_id["cn_m.m2_yoy"]["points"][-1] == {
        "period": "202606",
        "value": 8.1,
    }
    assert by_id["cn_pmi.headline"]["points"][-1]["value"] == 50.1
    assert by_id["shibor.on"]["points"][-1] == {
        "period": "20260710",
        "value": 1.307,
    }
    assert payload["axis_mode"] == "separate"


def test_macro_catalog_cursors_overlap_latest_release_period():
    catalog = economy_catalog()
    monthly = catalog.get("cn_cpi").parameter_builder
    quarterly = catalog.get("cn_gdp").parameter_builder
    daily = catalog.get("shibor").parameter_builder
    assert monthly is not None and quarterly is not None and daily is not None

    assert monthly(
        SyncRequest(domain="economy"), {"cursor": "202606"}
    ) == {"start_m": "202606"}
    assert quarterly(
        SyncRequest(domain="economy"), {"cursor": "2026Q1"}
    ) == {"start_q": "2026Q1"}
    assert daily(
        SyncRequest(domain="economy"), {"cursor": "20260710"}
    ) == {"start_date": "20260710"}
    assert monthly(
        SyncRequest(
            domain="economy",
            params={"start_m": "202501", "end_m": "202512"},
        ),
        {"cursor": "202606"},
    ) == {"start_m": "202501", "end_m": "202512"}


@pytest.mark.parametrize("max_points", [0, 2001])
def test_macro_service_rejects_unbounded_point_limits(service, max_points):
    with pytest.raises(ValueError, match="max_points"):
        service.series(["cn_cpi.nt_yoy"], max_points=max_points)


def test_macro_service_rejects_unknown_or_excessive_series(service):
    with pytest.raises(ValueError, match="unknown macro series"):
        service.series(["cn_cpi.missing"], max_points=120)
    with pytest.raises(ValueError, match="at most"):
        service.series(["cn_cpi.nt_yoy"] * 13, max_points=120)
