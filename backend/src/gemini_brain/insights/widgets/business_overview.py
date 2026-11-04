from pydantic import AliasChoices, BaseModel, Field

from gemini_brain.insights.analytics.util import ensure_labels, fmt_money, fmt_pct, pct_change, safe_ratio
from gemini_brain.insights.schemas.response import Bullet, Chart, ChartSeries
from gemini_brain.insights.widgets.base import WidgetAnalysis


class BusinessOverviewData(BaseModel):
    labels: list[str] | None = Field(default=None, validation_alias=AliasChoices("labels", "categories"))
    revenue: list[float]
    expenses: list[float]
    ebitda: list[float]


DataModel = BusinessOverviewData


def analyze(data: BusinessOverviewData) -> WidgetAnalysis:
    revenue, expenses, ebitda = data.revenue, data.expenses, data.ebitda
    labels = ensure_labels(data.labels, len(revenue))

    metric_bullets = [
        Bullet(type="metric", label=labels[i], value=ebitda[i] if i < len(ebitda) else None, trend="flat")
        for i in range(len(labels))
    ]

    latest_rev = revenue[-1] if revenue else 0
    latest_ebitda = ebitda[-1] if ebitda else 0
    prev_ebitda = ebitda[-2] if len(ebitda) > 1 else None
    ebitda_margin = safe_ratio(latest_ebitda, latest_rev)
    ebitda_change = pct_change(latest_ebitda, prev_ebitda) if prev_ebitda is not None else None

    lines = [
        f"Latest period ({labels[-1] if labels else 'n/a'}): revenue {fmt_money(latest_rev)}, "
        f"expenses {fmt_money(expenses[-1] if expenses else 0)}, EBITDA {fmt_money(latest_ebitda)}.",
        f"EBITDA margin (latest period): {fmt_pct(ebitda_margin)}.",
        f"EBITDA change vs previous period: {fmt_pct(ebitda_change)}.",
    ]
    if len(labels) > 1:
        history = ", ".join(
            f"{labels[i]}: rev {fmt_money(revenue[i])} / exp {fmt_money(expenses[i])} / ebitda {fmt_money(ebitda[i])}"
            for i in range(len(labels))
            if i < len(revenue) and i < len(expenses) and i < len(ebitda)
        )
        lines.append(f"Full period history: {history}.")

    chart = Chart(
        type="line",
        labels=labels,
        series=[
            ChartSeries(name="Revenue", data=revenue),
            ChartSeries(name="Expenses", data=expenses),
            ChartSeries(name="EBITDA", data=ebitda),
        ],
    )

    return WidgetAnalysis(facts_text="\n".join(lines), metric_bullets=metric_bullets, chart=chart)
