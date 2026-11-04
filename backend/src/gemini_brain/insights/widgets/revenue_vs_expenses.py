from pydantic import AliasChoices, BaseModel, Field, field_validator

from gemini_brain.insights.analytics.util import ensure_labels, fmt_money, fmt_pct, pct_change, safe_ratio, trend
from gemini_brain.insights.schemas.response import Bullet, Chart, ChartSeries
from gemini_brain.insights.widgets.base import WidgetAnalysis


class RevenueVsExpensesData(BaseModel):
    # "categories" accepted as an alias -- some callers send period labels
    # under that name (matches the profit-loss widget's own field name)
    # instead of "labels". Labels are optional entirely: if omitted, generic
    # P1/P2/... labels are generated from the data length rather than
    # rejecting an otherwise valid payload.
    labels: list[str] | None = Field(default=None, validation_alias=AliasChoices("labels", "categories"))
    revenue: list[float]
    expenses: list[float]

    @field_validator("expenses")
    @classmethod
    def _lengths_match(cls, v, info):
        revenue = info.data.get("revenue")
        if revenue is not None and len(v) != len(revenue):
            raise ValueError("expenses length must match revenue length")
        return v


DataModel = RevenueVsExpensesData


def analyze(data: RevenueVsExpensesData) -> WidgetAnalysis:
    revenue, expenses = data.revenue, data.expenses
    labels = ensure_labels(data.labels, len(revenue))

    metric_bullets: list[Bullet] = []
    for i, label in enumerate(labels):
        prev_rev = revenue[i - 1] if i > 0 else None
        metric_bullets.append(
            Bullet(
                type="metric",
                label=label,
                value=revenue[i],
                trend=trend(revenue[i], prev_rev) if prev_rev is not None else "flat",
            )
        )

    latest_rev = revenue[-1] if revenue else 0
    latest_exp = expenses[-1] if expenses else 0
    prev_rev = revenue[-2] if len(revenue) > 1 else None
    net_profit = latest_rev - latest_exp
    margin = safe_ratio(net_profit, latest_rev)
    expense_ratio = safe_ratio(latest_exp, latest_rev)
    mom_change = pct_change(latest_rev, prev_rev) if prev_rev is not None else None

    lines = [
        f"Latest period ({labels[-1] if labels else 'n/a'}): revenue {fmt_money(latest_rev)}, "
        f"expenses {fmt_money(latest_exp)}, net profit {fmt_money(net_profit)}.",
        f"Revenue change vs previous period: {fmt_pct(mom_change)}.",
        f"Profit margin (latest period): {fmt_pct(margin)}.",
        f"Expense ratio, i.e. expenses as a percent of revenue (latest period): {fmt_pct(expense_ratio)}.",
    ]
    if len(revenue) > 1:
        history = ", ".join(
            f"{labels[i]}: revenue {fmt_money(revenue[i])} / expenses {fmt_money(expenses[i])}"
            for i in range(len(labels))
        )
        lines.append(f"Full period history: {history}.")

    chart = Chart(
        type="bar",
        labels=labels,
        series=[
            ChartSeries(name="Revenue", data=revenue),
            ChartSeries(name="Expenses", data=expenses),
        ],
    )

    return WidgetAnalysis(
        facts_text="\n".join(lines),
        metric_bullets=metric_bullets,
        chart=chart,
    )
