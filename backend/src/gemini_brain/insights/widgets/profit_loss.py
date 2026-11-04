from pydantic import AliasChoices, BaseModel, Field

from gemini_brain.insights.analytics.util import fmt_money, fmt_pct, pct_change, safe_ratio, trend
from gemini_brain.insights.schemas.response import Bullet, Chart, ChartSeries
from gemini_brain.insights.widgets.base import WidgetAnalysis


class MonthlyTrend(BaseModel):
    # "labels" accepted as an alias -- matches the field name the other
    # time-series widgets use, for callers that send that name instead.
    categories: list[str] = Field(default_factory=list, validation_alias=AliasChoices("categories", "labels"))
    income: list[float] = []
    expense: list[float] = []


class ProfitLossData(BaseModel):
    """
    Mirrors what ProfitLossView.jsx actually holds in memory -- NOT the raw
    ReportService.getProfitLossReport() response shape. The component
    consumes a different prop (enhancedPLData, from
    GET /report/profit-loss-with-accounts) and locally computes its own
    flat `metrics` object (see ProfitLossView.jsx lines 104-127) before
    ever rendering anything -- that computed object, camelCase field names
    included, is what gets forwarded here as-is.
    """

    totalIncome: float
    totalExpenses: float
    netProfit: float
    profitMargin: float
    monthlyTrend: MonthlyTrend | None = None


DataModel = ProfitLossData


def analyze(data: ProfitLossData) -> WidgetAnalysis:
    expense_ratio = safe_ratio(data.totalExpenses, data.totalIncome)

    metric_bullets = [
        Bullet(type="metric", label="Total Income", value=data.totalIncome),
        Bullet(type="metric", label="Total Expenses", value=data.totalExpenses),
        Bullet(type="metric", label="Net Profit", value=data.netProfit),
    ]

    lines = [
        f"Total income: {fmt_money(data.totalIncome)}. Total expenses: {fmt_money(data.totalExpenses)}. "
        f"Net profit: {fmt_money(data.netProfit)}.",
        # profitMargin is trusted as given (the frontend already computed it)
        # rather than recomputed here, consistent with treating the payload
        # as source of truth.
        f"Profit margin: {data.profitMargin:.1f}%.",
        f"Expenses as a percent of income (expense ratio): {fmt_pct(expense_ratio)}.",
    ]

    chart = Chart(
        type="bar",
        labels=["Income", "Expenses", "Net Profit"],
        series=[ChartSeries(name="Amount", data=[data.totalIncome, data.totalExpenses, data.netProfit])],
    )

    if data.monthlyTrend and data.monthlyTrend.categories:
        mt = data.monthlyTrend
        for i, label in enumerate(mt.categories):
            prev = mt.income[i - 1] if i > 0 and i - 1 < len(mt.income) else None
            metric_bullets.append(
                Bullet(
                    type="metric",
                    label=label,
                    value=mt.income[i] if i < len(mt.income) else None,
                    trend=trend(mt.income[i], prev) if prev is not None and i < len(mt.income) else "flat",
                )
            )
        if len(mt.income) > 1:
            latest_change = pct_change(mt.income[-1], mt.income[-2])
            lines.append(
                f"Income change vs previous month: {fmt_pct(latest_change)}."
            )
        history = ", ".join(
            f"{mt.categories[i]}: income {fmt_money(mt.income[i])} / expense {fmt_money(mt.expense[i])}"
            for i in range(len(mt.categories))
            if i < len(mt.income) and i < len(mt.expense)
        )
        if history:
            lines.append(f"Monthly history: {history}.")

        chart = Chart(
            type="bar",
            labels=mt.categories,
            series=[
                ChartSeries(name="Income", data=mt.income),
                ChartSeries(name="Expense", data=mt.expense),
            ],
        )

    return WidgetAnalysis(facts_text="\n".join(lines), metric_bullets=metric_bullets, chart=chart)
