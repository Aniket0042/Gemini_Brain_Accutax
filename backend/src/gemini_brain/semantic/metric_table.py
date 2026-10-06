"""
metric_table.py — Multi-org metric figures from Cube, one query per view.

Phase 1 bridge for orchestrator/multi_org.py. Keys match
multi_org_metrics.METRICS; every organization comes back from one Cube query
per view instead of one SQL report per organization. Growth and series
metrics are not mapped: they need two windows or a grain, and arrive with
the agent in Phase 2.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from gemini_brain.semantic import cube_client

MEMBER_FOR_METRIC: Dict[str, str] = {
    "revenue": "pnl.revenue",
    "expenses": "pnl.total_expenses",
    "net_profit": "pnl.net_profit",
    "profit_margin": "pnl.net_margin_pct",
    "cash_balance": "balance_sheet.cash_and_bank",
    "total_assets": "balance_sheet.assets",
    "total_liabilities": "balance_sheet.liabilities",
    "total_equity": "balance_sheet.total_equity",
    "output_vat": "vat.output_vat",
    "input_vat": "vat.input_vat",
    "vat_payable": "vat.net_vat_payable",
    "receivables": "receivables.outstanding",
    "overdue_receivables": "receivables.overdue_amount",
    "payables": "payables.outstanding",
    "overdue_payables": "payables.overdue_amount",
}

#: Views whose figures cover a period, and the time dimension the period applies to.
FLOW_TIME_DIMENSION = {"pnl": "pnl.transaction_date", "vat": "vat.document_date"}
BALANCE_TIME_DIMENSION = {"balance_sheet": "balance_sheet.transaction_date"}
BEGINNING_OF_TIME = "1900-01-01"

Table = Dict[int, Dict[str, Optional[Decimal]]]


def metric_table(
    metric_keys: Sequence[str],
    *,
    organization_ids: Sequence[int],
    start: str,
    end: str,
    as_of: str,
    subject: str,
    deadline: float,
) -> Tuple[Table, List[int]]:
    """({org: {metric: value}}, orgs with figures in more than one currency).

    A metric missing for an org means nothing was recorded, not zero. A metric
    set to None means the org's figures span currencies and were not added up.
    Unmapped keys are ignored.
    """
    by_view: Dict[str, List[Tuple[str, str]]] = {}
    for key in metric_keys:
        member = MEMBER_FOR_METRIC.get(key)
        if member:
            by_view.setdefault(member.split(".", 1)[0], []).append((key, member))

    table: Table = {int(o): {} for o in organization_ids}
    mixed: List[int] = []
    for view, pairs in by_view.items():
        query: Dict[str, object] = {"measures": [m for _, m in pairs], "dimensions": [f"{view}.organization_id"]}
        if view in FLOW_TIME_DIMENSION:
            query["timeDimensions"] = [{"dimension": FLOW_TIME_DIMENSION[view], "dateRange": [start, end]}]
        elif view in BALANCE_TIME_DIMENSION:
            query["timeDimensions"] = [{"dimension": BALANCE_TIME_DIMENSION[view],
                                        "dateRange": [BEGINNING_OF_TIME, as_of]}]
            query["dimensions"] = [f"{view}.organization_id", f"{view}.account_currency"]
        result = cube_client.load(query, organization_ids=organization_ids, subject=subject, deadline=deadline)

        rows_by_org: Dict[int, list] = {}
        for row in result.rows:
            rows_by_org.setdefault(int(row[f"{view}.organization_id"]), []).append(row)
        for org, rows in rows_by_org.items():
            if len(rows) > 1:
                mixed.append(org)
            for key, member in pairs:
                table[org][key] = rows[0].get(member) if len(rows) == 1 else None
    return table, sorted(set(mixed))
