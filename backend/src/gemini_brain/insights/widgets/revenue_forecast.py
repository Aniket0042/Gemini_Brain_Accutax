from pydantic import AliasChoices, BaseModel, Field

from gemini_brain.insights.analytics.util import ensure_labels, fmt_money, fmt_pct, pct_change
from gemini_brain.insights.schemas.response import Bullet, Chart, ChartSeries
from gemini_brain.insights.widgets.base import WidgetAnalysis


class RevenueForecastData(BaseModel):
    labels: list[str] | None = Field(default=None, validation_alias=AliasChoices("labels", "categories"))
    actual: list[float | None]
    forecast: list[float | None]


DataModel = RevenueForecastData


def analyze(data: RevenueForecastData) -> WidgetAnalysis:
    actual, forecast = data.actual, data.forecast
    # actual/forecast are not required to be the same length -- some callers
    # send a "sequential" shape (N actual periods, then M more forecast-only
    # periods appended after) rather than two parallel same-length arrays
    # padded with nulls. Build one unified timeline covering both, treating
    # any position beyond an array's own length as null.
    n = max(len(actual), len(forecast))
    labels = ensure_labels(data.labels, n)

    padded_actual = [actual[i] if i < len(actual) else None for i in range(n)]
    padded_forecast = [forecast[i] if i < len(forecast) else None for i in range(n)]

    metric_bullets = [
        Bullet(
            type="metric",
            label=labels[i],
            value=padded_actual[i] if padded_actual[i] is not None else padded_forecast[i],
            trend="flat",
        )
        for i in range(n)
    ]

    actual_points = [v for v in padded_actual if v is not None]
    last_actual = actual_points[-1] if actual_points else None

    # The forecast point at the same index as the last actual period is
    # usually a duplicate of that actual value, included only so a chart
    # line connects the two series smoothly -- not a genuine prediction.
    # The real "next" forecast is the first point strictly beyond where
    # actual data ends.
    future_start = len(actual)
    next_forecast = padded_forecast[future_start] if future_start < len(padded_forecast) else None
    if next_forecast is None:
        forecast_points = [v for v in padded_forecast if v is not None]
        next_forecast = forecast_points[0] if forecast_points else None

    growth = (
        pct_change(next_forecast, last_actual)
        if last_actual is not None and next_forecast is not None
        else None
    )

    lines = [
        f"Last actual figure: {fmt_money(last_actual) if last_actual is not None else 'n/a'}.",
        f"Next forecast figure: {fmt_money(next_forecast) if next_forecast is not None else 'n/a'}.",
        f"Forecast growth vs last actual: {fmt_pct(growth)}.",
    ]
    forecast_history = [
        f"{labels[i]}: {fmt_money(padded_forecast[i])}" for i in range(n) if padded_forecast[i] is not None
    ]
    if forecast_history:
        lines.append("Full forecast horizon: " + ", ".join(forecast_history) + ".")

    chart = Chart(
        type="line",
        labels=labels,
        series=[
            ChartSeries(name="Actual", data=padded_actual),
            ChartSeries(name="Forecast", data=padded_forecast),
        ],
    )

    return WidgetAnalysis(facts_text="\n".join(lines), metric_bullets=metric_bullets, chart=chart)
