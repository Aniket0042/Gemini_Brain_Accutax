from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from gemini_brain.insights.schemas.request import WidgetId
from gemini_brain.insights.widgets import business_overview, kpi_sparkline, profit_loss, revenue_forecast, revenue_vs_expenses
from gemini_brain.insights.widgets.base import WidgetAnalysis


@dataclass
class WidgetSpec:
    data_model: type[BaseModel]
    analyze: Callable[[Any], WidgetAnalysis]
    display_name: str


REGISTRY: dict[str, WidgetSpec] = {
    WidgetId.REVENUE_VS_EXPENSES.value: WidgetSpec(
        data_model=revenue_vs_expenses.DataModel,
        analyze=revenue_vs_expenses.analyze,
        display_name="Revenue vs Expenses",
    ),
    WidgetId.BUSINESS_OVERVIEW.value: WidgetSpec(
        data_model=business_overview.DataModel,
        analyze=business_overview.analyze,
        display_name="Business Overview",
    ),
    WidgetId.REVENUE_FORECAST.value: WidgetSpec(
        data_model=revenue_forecast.DataModel,
        analyze=revenue_forecast.analyze,
        display_name="Revenue Forecast",
    ),
    WidgetId.TOTAL_REVENUE.value: WidgetSpec(
        data_model=kpi_sparkline.DataModel,
        analyze=kpi_sparkline.make_analyze("Total Revenue"),
        display_name="Total Revenue",
    ),
    WidgetId.TOTAL_EXPENSES.value: WidgetSpec(
        data_model=kpi_sparkline.DataModel,
        analyze=kpi_sparkline.make_analyze("Total Expenses"),
        display_name="Total Expenses",
    ),
    WidgetId.NET_PROFIT.value: WidgetSpec(
        data_model=kpi_sparkline.DataModel,
        analyze=kpi_sparkline.make_analyze("Net Profit"),
        display_name="Net Profit",
    ),
    WidgetId.OUTSTANDING_BALANCE.value: WidgetSpec(
        data_model=kpi_sparkline.DataModel,
        analyze=kpi_sparkline.make_analyze("Outstanding Balance"),
        display_name="Outstanding Balance",
    ),
    WidgetId.CASH_BALANCE.value: WidgetSpec(
        data_model=kpi_sparkline.DataModel,
        analyze=kpi_sparkline.make_analyze("Cash Balance"),
        display_name="Cash Balance",
    ),
    WidgetId.PROFIT_LOSS.value: WidgetSpec(
        data_model=profit_loss.DataModel,
        analyze=profit_loss.analyze,
        display_name="Profit & Loss",
    ),
}


def get_widget_spec(widget_id: str) -> WidgetSpec:
    spec = REGISTRY.get(widget_id)
    if spec is None:
        raise KeyError(widget_id)
    return spec
