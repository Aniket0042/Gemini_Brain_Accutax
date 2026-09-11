"""Each file generator returns recognizable, non-empty bytes."""
from gemini_brain.artifacts.generate import (
    generate_and_store,
    render_csv,
    render_docx,
    render_pdf,
    render_pptx,
    render_xlsx,
)
from gemini_brain.artifacts.report_spec import build_report_spec
from gemini_brain.artifacts.store import reset_for_tests


SPEC = build_report_spec(
    {
        "report": "Total Income",
        "period": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        "summary": {"total_income": 1612654.5, "invoice_count": 12},
        "items": [
            {"contact_name": "Acme", "amount": 5000},
            {"contact_name": "Beta", "amount": 3000},
        ],
    },
    "export P&L",
    org_name="Demo Org",
)


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


def test_csv_utf8_bom_and_header():
    raw = render_csv(SPEC)
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"Contact Name" in raw or b"Amount" in raw


def test_xlsx_is_zip():
    raw = render_xlsx(SPEC)
    assert raw[:2] == b"PK"
    assert len(raw) > 100


def test_pdf_header():
    raw = render_pdf(SPEC)
    assert raw.startswith(b"%PDF")
    assert len(raw) > 100


def test_docx_is_zip():
    raw = render_docx(SPEC)
    assert raw[:2] == b"PK"


def test_pptx_is_zip():
    raw = render_pptx(SPEC)
    assert raw[:2] == b"PK"


def test_generate_and_store_artifact_block():
    block = generate_and_store(SPEC, "csv", user_id=14, organization_id=2)
    assert block["type"] == "artifact"
    assert block["filename"].endswith(".csv")
    assert block["expires_in"] == 120
    assert block["id"]
