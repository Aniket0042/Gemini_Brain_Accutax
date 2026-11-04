from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class WidgetId(str, Enum):
    REVENUE_VS_EXPENSES = "revenue-vs-expenses"
    BUSINESS_OVERVIEW = "business-overview"
    REVENUE_FORECAST = "revenue-forecast"
    TOTAL_REVENUE = "total-revenue"
    TOTAL_EXPENSES = "total-expenses"
    NET_PROFIT = "net-profit"
    OUTSTANDING_BALANCE = "outstanding-balance"
    CASH_BALANCE = "cash-balance"
    PROFIT_LOSS = "profit-loss"


class DateRange(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None
    granularity: Optional[str] = Field(
        default=None, description="month | quarter | year | yoy"
    )


class InsightRequest(BaseModel):
    widgetId: WidgetId
    organizationId: int
    dateRange: Optional[DateRange] = None
    data: dict[str, Any]

    class Config:
        use_enum_values = True
