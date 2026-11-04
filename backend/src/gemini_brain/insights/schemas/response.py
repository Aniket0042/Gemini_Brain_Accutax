from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class Bullet(BaseModel):
    type: Literal["metric", "warning", "tip", "observation"]
    label: Optional[str] = None
    value: Optional[float] = None
    trend: Optional[Literal["up", "down", "flat"]] = None
    text: Optional[str] = None


class ChartSeries(BaseModel):
    name: str
    data: list[Optional[float]]


class Chart(BaseModel):
    type: Literal["bar", "line"]
    labels: list[str]
    series: list[ChartSeries]


class InsightResponse(BaseModel):
    widgetId: str
    summary: str
    bullets: list[Bullet]
    chart: Optional[Chart] = None
    model: str
    generatedAt: datetime
    cached: bool = False
