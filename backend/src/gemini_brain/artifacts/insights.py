"""Chart takeaways: the one-line "so what" under every chart.

Computed from the chart's own validated values, never written by a model, so
a takeaway can only say what the data says. Every figure is formatted with
`ir.format_compact` at a precision that facts.extract_figures accepts, and
integrity.verify_document grounds each takeaway against the FactSheet like
any narrative sentence — a takeaway that ever failed would be dropped.

One rule per chart job:
  * share / ranking     — the leader, its share, and the top-3 share (or, with
                          negative values, the largest and the most negative);
  * several measures    — the leader on the first measure;
  * two figures         — how far apart they are (revenue vs expenses);
  * trend               — the change from first to last period and the peak;
  * two measures in time — how often one beat the other, and the widest gap;
  * composition in time — the largest part and its share of the whole;
  * profit bridge       — what the costs took out, and the biggest cost.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from gemini_brain.artifacts import theme
from gemini_brain.artifacts.ir import ChartSection, ReportDocument, format_compact

#: Title the builder gives the unrelated-KPI comparison chart; shares across
#: unrelated headline figures would be meaningless, so it gets no takeaway.
KPI_CHART_TITLE = "Key figures"

_HUNDRED = Decimal(100)


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    return part / whole * _HUNDRED


def _is_other(label: str) -> bool:
    return label.strip().lower() == "other"


def _points(chart: ChartSection, series_index: int = 0) -> List[Tuple[str, Decimal]]:
    return [(c, v) for c, v in zip(chart.categories, chart.series[series_index].values) if v is not None]


_opposing = theme.is_opposing


def _comparison(chart: ChartSection, currency: str) -> Optional[str]:
    points = [(c, v) for c, v in _points(chart) if not _is_other(c)]
    if len(points) != 2:
        return None
    (a_lbl, a), (b_lbl, b) = sorted(points, key=lambda p: p[1], reverse=True)
    return (
        f"{a_lbl} is {format_compact(a - b, chart.unit, currency)} above {b_lbl} "
        f"({format_compact(a, chart.unit, currency)} against {format_compact(b, chart.unit, currency)})."
    )


def _signed_ranking(chart: ChartSection, currency: str) -> Optional[str]:
    """Mixed signs (balances, net amounts): the largest and the most negative,
    with no shares — a share of a total that nets positives against negatives
    would mislead."""
    points = [(c, v) for c, v in _points(chart) if not _is_other(c)]
    if len(points) < 2:
        return None
    top_lbl, top = max(points, key=lambda p: p[1])
    low_lbl, low = min(points, key=lambda p: p[1])
    return (
        f"{top_lbl} is the largest at {format_compact(top, chart.unit, currency)}; "
        f"{low_lbl} is negative at {format_compact(low, chart.unit, currency)}."
    )


def _share_base(chart: ChartSection) -> str:
    """What a share is a share of: the whole, or only the N names shown."""
    return f"the {chart.ranked_subset} shown" if chart.ranked_subset else "the total"


def _multi_ranking(chart: ChartSection, currency: str) -> Optional[str]:
    """Several measures per category: lead with the first (the one asked about)."""
    points = [(c, v) for c, v in _points(chart) if not _is_other(c)]
    if len(points) < 2:
        return None
    name = chart.series[0].name
    lead_lbl, lead = max(points, key=lambda p: p[1])
    text = f"{lead_lbl} has the highest {name} at {format_compact(lead, chart.unit, currency)}"
    total = sum((v for _, v in _points(chart)), Decimal(0))
    if total > 0 and all(v >= 0 for _, v in _points(chart)):
        text += f", {format_compact(_pct(lead, total), 'percent')} of {_share_base(chart)}"
    return text + "."


def _ranking(chart: ChartSection, currency: str) -> Optional[str]:
    points = _points(chart)
    if any(v < 0 for _, v in points):
        return _signed_ranking(chart, currency)
    total = sum((v for _, v in points), Decimal(0))
    named = sorted(((c, v) for c, v in points if not _is_other(c)), key=lambda p: p[1], reverse=True)
    if total <= 0 or len(named) < 2:
        return None
    leader, lead_value = named[0]
    text = (
        f"{leader} is the largest at {format_compact(lead_value, chart.unit, currency)}, "
        f"{format_compact(_pct(lead_value, total), 'percent')} of {_share_base(chart)}."
    )
    if len(named) >= 4:
        top3 = sum((v for _, v in named[:3]), Decimal(0))
        of = f" of {_share_base(chart)}" if chart.ranked_subset else ""
        text += f" The top three make up {format_compact(_pct(top3, total), 'percent')}{of}."
    return text


def _trend(chart: ChartSection, currency: str) -> Optional[str]:
    points = _points(chart)
    if len(points) < 2:
        return None
    (first_lbl, first), (last_lbl, last) = points[0], points[-1]
    name = chart.series[0].name
    if first == 0:
        text = f"{name} moved from {format_compact(first, chart.unit, currency)} in {first_lbl} to {format_compact(last, chart.unit, currency)} in {last_lbl}."
    else:
        change = _pct(last - first, abs(first))
        if abs(change) < Decimal("0.5"):
            text = f"{name} held steady at about {format_compact(last, chart.unit, currency)} from {first_lbl} to {last_lbl}."
        else:
            verb = "rose" if change > 0 else "fell"
            text = (
                f"{name} {verb} {format_compact(abs(change), 'percent')} from {first_lbl} to {last_lbl}, "
                f"from {format_compact(first, chart.unit, currency)} to {format_compact(last, chart.unit, currency)}."
            )
    if len(points) >= 3:
        peak_lbl, peak = max(points, key=lambda p: p[1])
        if peak_lbl != last_lbl:
            text += f" It peaked in {peak_lbl} at {format_compact(peak, chart.unit, currency)}."
    return text


def _two_measures(chart: ChartSection, currency: str, *, timeish: bool = True) -> Optional[str]:
    a, b = chart.series[0], chart.series[1]
    pairs = [(c, x, y) for c, x, y in zip(chart.categories, a.values, b.values) if x is not None and y is not None]
    if not pairs:
        return None
    ahead = sum(1 for _, x, y in pairs if x > y)
    widest_lbl, wx, wy = max(pairs, key=lambda p: abs(p[1] - p[2]))
    noun = "period" if timeish else "category"
    periods = noun if len(pairs) == 1 else ("periods" if timeish else "categories")
    lead, trail = (a.name, b.name) if ahead * 2 >= len(pairs) else (b.name, a.name)
    count = ahead if lead == a.name else len(pairs) - ahead
    return (
        f"{lead} was above {trail} in {count} of {len(pairs)} {periods}; "
        f"the widest gap was {format_compact(abs(wx - wy), chart.unit, currency)} in {widest_lbl}."
    )


def _composition(chart: ChartSection, currency: str) -> Optional[str]:
    totals = []
    for s in chart.series:
        vals = [v for v in s.values if v is not None]
        totals.append((s.name, sum(vals, Decimal(0))))
    combined = sum((t for _, t in totals), Decimal(0))
    if combined <= 0:
        return None
    name, top = max(totals, key=lambda p: p[1])
    return (
        f"{name} is the largest part at {format_compact(top, chart.unit, currency)}, "
        f"{format_compact(_pct(top, combined), 'percent')} of the combined total."
    )


def _bridge(chart: ChartSection, currency: str) -> Optional[str]:
    vals = chart.series[0].values
    start, end = vals[0], vals[-1]
    steps = [(c, v) for i, (c, v) in enumerate(zip(chart.categories, vals))
             if i not in chart.total_indices and v is not None]
    costs = [(c, v) for c, v in steps if v < 0]
    if start is None or end is None or not costs:
        return None
    taken = sum((v for _, v in costs), Decimal(0))
    biggest_lbl, biggest = min(costs, key=lambda p: p[1])
    return (
        f"Costs of {format_compact(abs(taken), 'money', currency)} take {chart.categories[0].lower()} of "
        f"{format_compact(start, 'money', currency)} to {chart.categories[-1].lower()} of "
        f"{format_compact(end, 'money', currency)}; {biggest_lbl} is the largest cost at "
        f"{format_compact(abs(biggest), 'money', currency)}."
    )


def chart_takeaway(chart: ChartSection, currency: str = "AED") -> Optional[str]:
    from gemini_brain.artifacts.report_spec import _is_time_axis

    if chart.title == KPI_CHART_TITLE:
        return None
    if chart.chart_type == "waterfall":
        return _bridge(chart, currency)
    timeish = _is_time_axis(chart.categories)
    if len(chart.series) >= 2 and _opposing([s.name for s in chart.series]):
        return _two_measures(chart, currency, timeish=timeish)
    if chart.chart_type == "stacked_bar":
        return _composition(chart, currency)
    if timeish and len(chart.series) >= 2:
        return _two_measures(chart, currency)
    if timeish:
        return _trend(chart, currency)
    if len(chart.series) == 1:
        named = [c for c in chart.categories if not _is_other(c)]
        if len(named) == 2 or _opposing(chart.categories):
            return _comparison(chart, currency)
        return _ranking(chart, currency)
    return _multi_ranking(chart, currency)


def add_takeaways(doc: ReportDocument) -> ReportDocument:
    """Return `doc` with a takeaway on every chart that has something to say."""
    sections: List = []
    for s in doc.sections:
        if s.kind == "chart" and not s.takeaway:
            text = chart_takeaway(s, doc.meta.currency)
            if text:
                s = s.model_copy(update={"takeaway": text})
        sections.append(s)
    return doc.model_copy(update={"sections": sections})


__all__: Sequence[str] = ("chart_takeaway", "add_takeaways", "KPI_CHART_TITLE")
