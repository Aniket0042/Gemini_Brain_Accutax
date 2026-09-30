"""Offline tests for scripts/ops/check_guide_drift.py using synthetic bundles."""
import importlib.util
from pathlib import Path

from gemini_brain.knowledge.guide_loader import SECTION_ROUTES

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "check_guide_drift.py"
_spec = importlib.util.spec_from_file_location("check_guide_drift", _SCRIPT)
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)


def _bundle_with(routes):
    return "".join(f's.jsx(cr,{{path:"{r}",element:x}})' for r in routes)


def _all_routes():
    return {r.split("?", 1)[0] for r in SECTION_ROUTES.values()}


def test_no_route_drift_when_every_route_defined():
    assert drift.check_routes(_bundle_with(_all_routes())) == []


def test_removed_route_is_reported():
    routes = _all_routes() - {"/tax-rules"}
    missing = drift.check_routes(_bundle_with(routes))
    assert ("Creating a Tax Rule", "/tax-rules") in missing


def test_parameterised_route_needs_known_slug():
    routes = (_all_routes() - {"/reports/profit-loss"}) | {"/reports/:reportType"}
    assert drift.check_routes(_bundle_with(routes) + '"profit-loss"') == []
    missing = drift.check_routes(_bundle_with(routes))
    assert any(route == "/reports/profit-loss" for _, route in missing)


def test_renamed_label_is_reported():
    guide = "Click **Create Purchase Order** then **Save**."
    assert drift.check_labels('"Create Purchase Order" "Save"', guide) == []
    assert drift.check_labels('"New PO" "Save"', guide) == ["Create Purchase Order"]


def test_label_extraction_skips_noise():
    guide = "**Important:** go to **Sales > Invoice(s)**, click **+ Add Tax Rate**, " \
            "see **1a–1g** and **Reconcile (N)**, set **Effective From / Effective To**."
    labels = drift._labels(guide)
    assert {"Sales", "Invoice(s)", "Add Tax Rate", "Reconcile", "Effective From", "Effective To"} <= labels
    assert not any(l.startswith(("Important", "1a")) for l in labels)
