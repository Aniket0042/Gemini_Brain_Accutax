"""Fixes from the 19 Nov manual test of the medium VAT questions: quarter months from the VAT settings, the
self-assessed reverse-charge VAT figure, and the VAT 201 Excel export."""
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import yaml

from gemini_brain.artifacts.report_spec import build_report_spec
from gemini_brain.vat_return import tool as vr

D = dt.date
TODAY = D(2026, 11, 19)
MODEL = Path(__file__).resolve().parents[3] / "semantic" / "model"


def _load(groups=None, freqs=None, fail_groups=False):
    def load(query, *, organization_ids, subject, deadline):
        dims = query.get("dimensions", [])
        if "vat_settings.quarter_group" in dims:
            if fail_groups:
                raise vr.CubeError("unknown member vat_settings.quarter_group")
            return SimpleNamespace(rows=[{"vat_settings.organization_id": o, "vat_settings.quarter_group": g}
                                         for o, g in (groups or {}).items()])
        if "vat_settings.filing_frequency" in dims:
            return SimpleNamespace(rows=[{"vat_settings.organization_id": o, "vat_settings.filing_frequency": f}
                                         for o, f in (freqs or {}).items()])
        return SimpleNamespace(rows=[])
    return load


def test_quarter_groups_from_the_settings(monkeypatch):
    monkeypatch.setattr(vr.cube_client, "load", _load(groups={10182: 1, 10183: 2, 10173: 3, 9: "x"}))
    assert vr.quarter_groups([10182, 10183, 10173, 9], "t", 1e12) == {10182: 1, 10183: 2, 10173: 3}


def test_an_older_cube_without_the_field_gives_no_groups(monkeypatch):
    monkeypatch.setattr(vr.cube_client, "load", _load(fail_groups=True))
    assert vr.quarter_groups([10182], "t", 1e12) == {}


def test_last_quarter_follows_the_settings_quarter_months():
    # Summit: quarters end Jan/Apr/Jul/Oct. On 19 Nov 2026 the last one is Aug-Oct, due 28 Nov.
    p = vr.resolve_period({"which": "last"}, 10182, TODAY, {10182: "quarterly"}, None, {10182: 1})
    assert (p["start"], p["end"], p["due"]) == (D(2026, 8, 1), D(2026, 10, 31), D(2026, 11, 28))
    assert p["is_tax_period"] and p["quarter_source"] == "from the organization's VAT settings"


def test_a_quarter_in_the_settings_is_a_tax_period_with_a_due_date():
    # Bright Minds: quarters end Feb/May/Aug/Nov, so Jun-Aug is its quarter, due 28 Sep.
    p = vr.resolve_period({"which": "period", "period": {"start": "2026-06-01", "end": "2026-08-31"}}, 10183, TODAY,
                          {10183: "quarterly"}, None, {10183: 2})
    assert p["is_tax_period"] and p["due"] == D(2026, 9, 28)


def test_without_quarter_months_calendar_quarters_are_assumed_and_said():
    p = vr.resolve_period({"which": "last"}, 7, TODAY, {7: "quarterly"}, None, {})
    assert (p["start"], p["end"]) == (D(2026, 7, 1), D(2026, 9, 30))
    assert p["quarter_source"].startswith("assumed")


def test_a_monthly_filer_has_no_quarter_source():
    assert vr.resolve_period({"which": "last"}, 7, TODAY, {7: "monthly"}, None, {})["quarter_source"] is None


def test_the_vat_return_says_when_quarters_were_assumed(monkeypatch):
    monkeypatch.setattr(vr.cube_client, "load", _load(freqs={7: "quarterly", 8: "quarterly"}, groups={8: 1}))
    monkeypatch.setattr(vr.semantic_periods, "today_in", lambda tz: TODAY)
    res = vr.run({"which": "last"}, organization_ids=[7, 8], subject="t", deadline=1e12)
    assumed = [n for n in res["notes"] if "calendar quarters" in n and "were assumed" in n]
    assert len(assumed) == 1 and "[7]" in assumed[0]
    by_org = {o["organization_id"]: o for o in res["organizations"]}
    assert by_org[8]["period"] == {"start": "2026-08-01", "end": "2026-10-31"}


# --- Cube: the self-assessed reverse-charge VAT and the quarter field ---------------------------------------------

def _cube(name):
    return yaml.safe_load((MODEL / "cubes" / f"{name}.yml").read_text(encoding="utf-8"))["cubes"][0]


def _view_members(name):
    view = yaml.safe_load((MODEL / "views" / f"{name}.yml").read_text(encoding="utf-8"))["views"][0]
    return {m if isinstance(m, str) else m.get("name") for c in view["cubes"] for m in c["includes"]}


def test_purchases_give_the_self_assessed_reverse_charge_vat():
    measures = {m["name"]: m for m in _cube("purchase_lines")["measures"]}
    assert "0.05" in measures["reverse_charge_vat"]["sql"]
    assert "NOT the self-assessed" in measures["reverse_charge_vat_recorded"]["description"]
    assert {"reverse_charge_vat", "reverse_charge_net"} <= _view_members("purchases")


def test_vat_settings_give_the_quarter_group():
    assert "quarter_group" in {d["name"] for d in _cube("vat_config_rows")["dimensions"]}
    assert "quarter_group" in _view_members("vat_settings")


# --- the VAT 201 export -------------------------------------------------------------------------------------------

def test_a_vat_201_export_is_a_table_in_box_order_with_no_chart():
    rows = [{"organization_id": 10176, "box": "1b", "label": "Standard-rated supplies in Dubai", "net": 541043.0,
             "vat": 27052.15, "status": "from_books"},
            {"organization_id": 10176, "box": "14", "label": "Payable tax for the period", "net": None,
             "vat": 17370.66, "status": "approximate"}]
    spec = build_report_spec(rows, "export the VAT 201 draft to excel as a bar chart", chart_hint="bar")
    assert spec["charts"] == []
    assert [c["key"] for c in spec["tables"][0]["columns"]][:5] == ["box", "label", "net", "vat", "status"]


def test_other_exports_still_get_charts():
    rows = [{"month": f"2026-0{i}", "net_sales": 1000.0 * i} for i in range(1, 7)]
    assert build_report_spec(rows, "sales by month chart", chart_hint="bar")["charts"]
