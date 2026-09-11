from gemini_brain.artifacts.attach import attach_delivery, NOTHING_TO_EXPORT


def test_no_delivery_leaves_blocks_alone():
    blocks = [{"type": "table", "rows": []}]
    out = attach_delivery("total income this year", {"total_income": 10}, blocks)
    assert out == blocks


def test_plain_pnl_statement_does_not_add_a_chart():
    data = {
        "operating_income": 1777028.25,
        "operating_expense": 473626.40,
        "monthly": [
            {"month": "Jan", "revenue": 100, "expenses": 40},
            {"month": "Feb", "revenue": 120, "expenses": 50},
        ],
    }
    out = attach_delivery(
        "Generate our profit and loss statement for this fiscal year, broken down by month",
        data,
        [{"type": "kpi_grid", "items": []}],
    )
    types = [b["type"] for b in out]
    assert "chart" not in types
    assert "canvas" not in types


def test_chart_request_adds_canvas():
    data = [{"name": "Acme", "amount": 100}, {"name": "Beta", "amount": 50}]
    out = attach_delivery("graph revenue by customer", data, [])
    types = [b["type"] for b in out]
    assert "canvas" in types
    spec = next(b for b in out if b["type"] == "canvas")["spec"]
    assert spec["charts"]
    assert "chart" in types


def test_existing_chart_block_is_merged_into_spec():
    blocks = [{
        "type": "chart",
        "chart_type": "bar",
        "title": "Monthly Revenue vs Expenses",
        "categories": ["April", "May"],
        "series": [
            {"name": "Revenue", "data": [1, 2]},
            {"name": "Expenses", "data": [0.5, 0.7]},
        ],
    }]
    out = attach_delivery("visualize this", {"total_income": 10, "total_expense": 4}, blocks)
    spec = next(b for b in out if b["type"] == "canvas")["spec"]
    assert spec["charts"]
    assert spec["charts"][0]["categories"] == ["April", "May"]


def test_file_without_data_refuses():
    out = attach_delivery("send me a pdf", None, [], status="ok")
    assert any(b.get("text") == NOTHING_TO_EXPORT for b in out)


def test_left_path_empty_results_refuses_export():
    out = attach_delivery("export as excel", None, [], status="ok")
    assert any(NOTHING_TO_EXPORT in (b.get("text") or "") for b in out)
