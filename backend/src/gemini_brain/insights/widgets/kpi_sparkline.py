from typing import Callable

from pydantic import BaseModel

from gemini_brain.insights.analytics.util import fmt_money, fmt_pct, trend
from gemini_brain.insights.schemas.response import Bullet, Chart, ChartSeries
from gemini_brain.insights.widgets.base import WidgetAnalysis


class KpiSparklineData(BaseModel):
    value: float
    change: float  # percent change vs previous period, as sent by the frontend
    sparkline: list[float]


DataModel = KpiSparklineData


def make_analyze(metric_label: str) -> Callable[[KpiSparklineData], WidgetAnalysis]:
    def analyze(data: KpiSparklineData) -> WidgetAnalysis:
        points = data.sparkline or [data.value]
        labels = [f"p{i + 1}" for i in range(len(points))]

        metric_bullets = [
            Bullet(
                type="metric",
                label=labels[i],
                value=points[i],
                trend=trend(points[i], points[i - 1]) if i > 0 else "flat",
            )
            for i in range(len(points))
        ]

        lines = [
            f"{metric_label} current value: {fmt_money(data.value)}.",
            f"{metric_label} change vs previous period: {fmt_pct(data.change)}.",
        ]
        if len(points) > 1:
            lines.append(
                f"Recent trend points for {metric_label}: "
                + ", ".join(fmt_money(p) for p in points)
                + "."
            )

        chart = Chart(
            type="bar",
            labels=labels,
            series=[ChartSeries(name=metric_label, data=points)],
        )

        return WidgetAnalysis(facts_text="\n".join(lines), metric_bullets=metric_bullets, chart=chart)

    return analyze
