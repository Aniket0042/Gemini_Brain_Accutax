"""Typed report model (the Report IR) — the one contract between report
building and every output (live canvas, PDF, DOCX, PPTX, XLSX, CSV, MD).

`report_spec.build_report_spec` still does the payload-shape guessing and
returns a loose dict. `ReportDocument.from_spec` turns that dict into this
model, and the model's validators are where invalid reports stop: a pie with
two series, a chart whose series length disagrees with its categories, money
and counts on one axis, a KPI labelled money with no currency. A section
that fails validation is dropped and replaced by a visible caveat — it never
reaches a renderer, and the export never crashes on it.

Numbers are `Decimal` inside the model (built from the value's string form,
so 0.1 stays 0.1), and every display string is produced by `format_value`,
the single formatter for reports. `to_spec()` projects the model back to the
JSON dict shape the frontend canvas and the file renderers already read.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Dict, List, Literal, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from gemini_brain.artifacts import theme

logger = logging.getLogger("gemini_brain.artifacts.ir")

MISSING = "—"
MAX_PIE_SLICES = 8

Unit = Literal["money", "count", "percent", "days", "number"]
ChartType = Literal["bar", "hbar", "stacked_bar", "line", "area", "pie", "donut", "waterfall"]
PART_TO_WHOLE = ("pie", "donut")

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_CENT = Decimal("0.01")


# ── Numbers ──────────────────────────────────────────────────────────────────

def to_decimal(v: Any) -> Optional[Decimal]:
    """Exact Decimal for a report value, or None when the value is missing.

    Floats go through str() so 0.1 becomes Decimal('0.1'), not the binary
    expansion. Non-finite values (NaN/inf) are treated as missing.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        d = v
    else:
        try:
            d = Decimal(str(v).replace(",", "").strip())
        except (InvalidOperation, ValueError):
            return None
    return d if d.is_finite() else None


def format_value(value: Optional[Decimal], unit: Unit, currency: Optional[str] = None) -> str:
    """The one display formatter for report numbers. Missing is "—", never 0."""
    if value is None:
        return MISSING
    if unit == "money":
        q = value.quantize(_CENT, rounding=ROUND_HALF_UP)
        return f"{currency or 'AED'} {q:,.2f}"
    if unit in ("count", "days"):
        return f"{value.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"
    if unit == "percent":
        return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"
    q = value.quantize(_CENT, rounding=ROUND_HALF_UP)
    return f"{q:,.2f}".rstrip("0").rstrip(".") if q != q.to_integral_value() else f"{q:,.0f}"


def format_compact(value: Optional[Decimal], unit: Unit, currency: Optional[str] = None) -> str:
    """Short form for chart labels and takeaways: "AED 63.7K", "AED 1.24M", "21.6%".

    Precision is chosen so the rounding stays inside what facts.extract_figures
    accepts for the same text — a takeaway always grounds against its data.
    """
    if value is None:
        return MISSING
    if unit == "percent":
        return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"
    if unit in ("count", "days"):
        return f"{value.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"
    sign = "-" if value < 0 else ""
    a = abs(value)
    if a >= 1_000_000:
        body = f"{(a / Decimal(1_000_000)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}M"
    elif a >= 10_000:
        body = f"{(a / Decimal(1_000)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}K"
    else:
        body = f"{a.quantize(Decimal(1), rounding=ROUND_HALF_UP):,}"
    return f"{currency or 'AED'} {sign}{body}" if unit == "money" else f"{sign}{body}"


def infer_unit(label: str) -> Unit:
    """Unit from a metric key or label, using the same key rules as the chat formatters."""
    from gemini_brain.tools.formatters import _is_count_key, _is_days_key, _is_money_key

    key = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    if any(t in key.split("_") for t in ("pct", "percent", "percentage", "margin", "rate")) or "%" in (label or ""):
        return "percent"
    if _is_days_key(key):
        return "days"
    if _is_count_key(key):
        return "count"
    if _is_money_key(key):
        return "money"
    return "number"


def _as_json_number(d: Optional[Decimal]) -> Optional[float]:
    return None if d is None else float(d)


# ── Building blocks ──────────────────────────────────────────────────────────

class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Measure(_Model):
    """One labelled number (a KPI). `display` is derived, never hand-written."""
    label: str = Field(min_length=1)
    value: Optional[Decimal] = None
    unit: Unit = "number"
    currency: Optional[str] = None
    display: str = ""
    source_ref: Optional[str] = None
    #: A non-numeric KPI (e.g. "Status: Active") keeps its text here, value None.
    text: Optional[str] = None

    @model_validator(mode="after")
    def _derive_display(self) -> "Measure":
        if self.unit == "money" and self.value is not None and not self.currency:
            raise ValueError(f"money measure {self.label!r} has no currency")
        display = self.text if (self.value is None and self.text) else format_value(self.value, self.unit, self.currency)
        if self.display != display:
            object.__setattr__(self, "display", display)
        return self


class Series(_Model):
    name: str = Field(min_length=1)
    values: List[Optional[Decimal]]


#: Retrieval tier (resilience.outcomes.Retrieved.tier) -> reader-facing source.
_TIER_LABELS = {
    "live_api": "Accutax ledger (live API)",
    "sql_report": "Accutax ledger (direct report query)",
    "sql_function": "Accutax ledger (direct report query)",
    "sql_verify": "Accutax ledger (direct database check)",
    "sql_fallback": "Accutax ledger (generated SQL query)",
    "cache": "Accutax ledger (cached result)",
}


class Provenance(_Model):
    """Where the report's numbers came from — printed on every output."""
    tier: Optional[str] = None
    endpoint: Optional[str] = None
    row_count: Optional[int] = None
    truncated: bool = False
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notice_code: Optional[str] = None
    notice_message: Optional[str] = None

    def source_label(self) -> str:
        return _TIER_LABELS.get(self.tier or "", "Accutax ledger")

    def source_line(self) -> str:
        bits = [f"Source: {self.source_label()}"]
        if self.row_count is not None:
            bits.append(f"{self.row_count:,} record{'s' if self.row_count != 1 else ''}"
                        + (" (truncated)" if self.truncated else ""))
        bits.append(f"retrieved {self.retrieved_at.astimezone(timezone.utc):%d %b %Y %H:%M} UTC")
        return " · ".join(bits)


class ReportMeta(_Model):
    title: str = Field(min_length=1)
    subtitle: str = ""
    entity: str = ""
    period: Optional[str] = None
    currency: str = "AED"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    generated_by: str = "Accutax AI"
    query: str = ""
    provenance: Optional[Provenance] = None


# ── Sections ─────────────────────────────────────────────────────────────────

class KpiSection(_Model):
    kind: Literal["kpis"] = "kpis"
    items: List[Measure] = Field(min_length=1)


class NarrativeSection(_Model):
    """The report's written analysis.

    A report narrative (narrator.py) is structured — headline, summary, drivers,
    risks, actions — and `markdown` is rendered from those parts by
    `from_parts`, so every output shows the same text in the same order. A
    narrative with no parts (the older chat-answer copy) is markdown only.
    """
    kind: Literal["narrative"] = "narrative"
    markdown: str = Field(min_length=1)
    insight: Optional[str] = None
    origin: Literal["report", "template", "chat"] = "chat"
    headline: Optional[str] = None
    summary: List[str] = Field(default_factory=list)
    drivers: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    #: A computed headline to use if grounding removes the written one.
    fallback_headline: Optional[str] = None

    @classmethod
    def from_parts(
        cls,
        *,
        origin: str,
        headline: Optional[str],
        summary: Sequence[str] = (),
        drivers: Sequence[str] = (),
        risks: Sequence[str] = (),
        actions: Sequence[str] = (),
    ) -> Optional["NarrativeSection"]:
        """Build the section and its markdown; None when every part is empty."""
        lines: List[str] = []
        if headline:
            lines += [f"**{headline}**", ""]
        if summary:
            lines += [" ".join(summary), ""]
        for title, items in (("Key drivers", drivers), ("Risks and watch-points", risks), ("Recommended actions", actions)):
            if items:
                lines += [f"## {title}"] + [f"- {item}" for item in items] + [""]
        markdown = "\n".join(lines).strip()
        if not markdown:
            return None
        return cls(
            markdown=markdown, insight=headline, origin=origin, headline=headline,
            summary=list(summary), drivers=list(drivers), risks=list(risks), actions=list(actions),
        )


class ChartSection(_Model):
    kind: Literal["chart"] = "chart"
    chart_type: ChartType
    title: str = ""
    caption: Optional[str] = None
    x_label: str = ""
    y_label: str = ""
    unit: Unit = "money"
    categories: List[str] = Field(min_length=1)
    series: List[Series] = Field(min_length=1)
    series_colors: List[str] = Field(default_factory=list)
    point_colors: Optional[List[str]] = None
    requested_type: Optional[str] = None
    downgrade_reason: Optional[str] = None
    #: One-line "so what", computed from the values (insights.py), never written by a model.
    takeaway: Optional[str] = None
    #: Waterfall only: indices of bars that are running totals (start, subtotals,
    #: end); every other bar is a change applied to the running total.
    total_indices: List[int] = Field(default_factory=list)
    #: The chart is the top (or bottom) N of a longer list the report does not
    #: have in full. Shares are of these N, never "of the total".
    ranked_subset: Optional[int] = None

    @field_validator("series_colors")
    @classmethod
    def _hex_series(cls, v: List[str]) -> List[str]:
        bad = [c for c in v if not _HEX.match(c or "")]
        if bad:
            raise ValueError(f"invalid colors {bad}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "ChartSection":
        n = len(self.categories)
        for s in self.series:
            if len(s.values) != n:
                raise ValueError(f"series {s.name!r} has {len(s.values)} values for {n} categories")
        if self.series_colors and len(self.series_colors) != len(self.series):
            raise ValueError("one color per series is required")
        if self.point_colors is not None:
            if len(self.point_colors) != n or any(not _HEX.match(c or "") for c in self.point_colors):
                raise ValueError("point colors must be one valid hex color per category")
        if all(v is None for s in self.series for v in s.values):
            raise ValueError("chart has no values at all")
        # One axis, one unit: money beside a count (or days, or %) on the same
        # scale makes the smaller one unreadable.
        units = {infer_unit(s.name) for s in self.series} - {"number"}
        if len(self.series) > 1 and len(units) > 1:
            raise ValueError(f"series mix units on one axis ({', '.join(sorted(units))})")
        if self.chart_type in PART_TO_WHOLE:
            if len(self.series) != 1:
                raise ValueError(f"a {self.chart_type} chart shows exactly one series")
            vals = [v for v in self.series[0].values if v is not None]
            if any(v < 0 for v in vals):
                raise ValueError(f"a {self.chart_type} chart cannot show negative values")
            if not any(v > 0 for v in vals):
                raise ValueError(f"a {self.chart_type} chart needs at least one value above zero")
            if n > MAX_PIE_SLICES:
                raise ValueError(f"a {self.chart_type} chart shows at most {MAX_PIE_SLICES} slices")
        if self.chart_type == "stacked_bar":
            if len(self.series) < 2:
                raise ValueError("a stacked chart needs two or more parts")
            if any(v is not None and v < 0 for s in self.series for v in s.values):
                raise ValueError("a stacked chart cannot stack negative values")
        if self.chart_type == "waterfall":
            self._check_bridge()
        elif self.total_indices:
            raise ValueError("only a waterfall chart has total bars")
        return self

    def _check_bridge(self) -> None:
        """Each total bar must equal the start plus every change before it."""
        if len(self.series) != 1:
            raise ValueError("a waterfall chart shows exactly one series")
        vals = self.series[0].values
        totals = sorted(set(self.total_indices))
        if not totals or totals[0] != 0 or totals[-1] != len(vals) - 1:
            raise ValueError("a waterfall chart starts and ends on a total bar")
        if any(v is None for v in vals):
            raise ValueError("a waterfall chart cannot have missing steps")
        running = vals[0]
        for i in range(1, len(vals)):
            if i in totals:
                if abs(running - vals[i]) > Decimal("0.01"):
                    raise ValueError(
                        f"bridge does not add up at {self.categories[i]!r}: steps give {running}, total is {vals[i]}"
                    )
                running = vals[i]
            else:
                running += vals[i]


class Column(_Model):
    key: str = Field(min_length=1)
    label: str = ""
    align: Literal["left", "right", "center"] = "left"
    unit: Optional[Unit] = None


class TableSection(_Model):
    kind: Literal["table"] = "table"
    title: str = "Details"
    columns: List[Column] = Field(min_length=1)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    raw_rows: List[Dict[str, Any]] = Field(default_factory=list)
    total_rows: int = 0
    truncated: bool = False
    period: Optional[str] = None
    hide_total_note: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> "TableSection":
        keys = [c.key for c in self.columns]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate table columns {keys}")
        if self.total_rows < len(self.rows):
            object.__setattr__(self, "total_rows", len(self.rows))
        return self


class CalloutSection(_Model):
    kind: Literal["callout"] = "callout"
    tone: Literal["note", "caveat", "warning"] = "note"
    text: str = Field(min_length=1)


class MethodologySection(_Model):
    kind: Literal["methodology"] = "methodology"
    lines: List[str] = Field(min_length=1)


Section = Annotated[
    Union[KpiSection, NarrativeSection, ChartSection, TableSection, CalloutSection, MethodologySection],
    Field(discriminator="kind"),
]


# ── Document ─────────────────────────────────────────────────────────────────

class ReportDocument(_Model):
    meta: ReportMeta
    sections: List[Section] = Field(default_factory=list)
    #: Outcome of integrity.verify_document (checks run/failed, narrative
    #: figures checked/verified, sentences removed). None until verified.
    integrity: Optional[Dict[str, int]] = None

    # Accessors, in document order.
    def of(self, kind: str) -> List[Any]:
        return [s for s in self.sections if s.kind == kind]

    @property
    def kpis(self) -> List[Measure]:
        return [m for s in self.of("kpis") for m in s.items]

    @property
    def charts(self) -> List[ChartSection]:
        return self.of("chart")

    @property
    def tables(self) -> List[TableSection]:
        return self.of("table")

    @property
    def narrative(self) -> Optional[NarrativeSection]:
        found = self.of("narrative")
        return found[0] if found else None

    def has_content(self) -> bool:
        return bool(self.kpis or self.charts or self.tables)

    # ── from the builder's loose dict ────────────────────────────────────────

    @classmethod
    def from_spec(
        cls,
        spec: Dict[str, Any],
        *,
        query: str = "",
        provenance: Optional[Provenance] = None,
    ) -> "ReportDocument":
        """Validate a builder spec into a document. Never raises for bad sections:
        each one that fails validation is dropped and replaced by a caveat."""
        currency = spec.get("currency") if isinstance(spec.get("currency"), str) else "AED"
        meta = ReportMeta(
            title=str(spec.get("title") or "Accutax report"),
            subtitle=str(spec.get("subtitle") or ""),
            entity=str(spec.get("entity") or ""),
            period=spec.get("period") or None,
            currency=currency or "AED",
            generated_at=_parse_dt(spec.get("generated_at")),
            query=query,
            provenance=provenance,
        )
        sections: List[Any] = []
        caveats: List[str] = []

        kpis = [m for m in (_measure(k, meta.currency) for k in spec.get("kpis") or []) if m is not None]
        if kpis:
            sections.append(KpiSection(items=kpis))

        narrative = (spec.get("narrative") or "").strip()
        if narrative:
            sections.append(NarrativeSection(markdown=narrative, insight=spec.get("insight")))

        for raw in spec.get("charts") or []:
            chart, problem = _chart(raw)
            if chart is not None:
                sections.append(chart)
            else:
                title = raw.get("title") or "A chart"
                logger.warning("Chart %r dropped from report: %s", title, problem)
                caveats.append(f"{title} could not be drawn from this data ({problem}).")

        for raw in spec.get("tables") or []:
            table, problem = _table(raw)
            if table is not None:
                sections.append(table)
            else:
                logger.warning("Table %r dropped from report: %s", raw.get("title"), problem)
                caveats.append(f"{raw.get('title') or 'A table'} could not be built from this data.")

        if provenance and provenance.notice_message:
            sections.append(CalloutSection(tone="caveat", text=provenance.notice_message))
        for note in spec.get("notes") or []:
            if isinstance(note, str) and note.strip():
                sections.append(CalloutSection(tone="note", text=note.strip()))
        for text in caveats:
            sections.append(CalloutSection(tone="warning", text=text))

        method = _methodology(meta, spec)
        if method:
            sections.append(MethodologySection(lines=method))
        return cls(meta=meta, sections=sections)

    # ── to the dict the canvas and renderers read ───────────────────────────

    def to_spec(self) -> Dict[str, Any]:
        meta = self.meta
        narrative = self.narrative
        notes = [s.text for s in self.of("callout")]
        methodology = [line for s in self.of("methodology") for line in s.lines]
        return {
            "title": meta.title,
            "subtitle": meta.subtitle,
            "entity": meta.entity,
            "period": meta.period,
            "generated_at": meta.generated_at.isoformat(),
            "generated_by": meta.generated_by,
            "currency": meta.currency,
            "kpis": [
                {
                    "label": m.label,
                    "value": _as_json_number(m.value),
                    "formatted": m.display,
                    "unit": m.unit,
                }
                for m in self.kpis
            ],
            "charts": [_chart_dict(c) for c in self.charts],
            "tables": [t.model_dump(exclude={"kind"}) for t in self.tables],
            "notes": notes,
            "callouts": [{"tone": s.tone, "text": s.text} for s in self.of("callout")],
            "insight": narrative.insight if narrative else None,
            "narrative": narrative.markdown if narrative else None,
            "narrative_parts": {
                "origin": narrative.origin,
                "headline": narrative.headline,
                "summary": list(narrative.summary),
                "drivers": list(narrative.drivers),
                "risks": list(narrative.risks),
                "actions": list(narrative.actions),
            } if narrative and narrative.origin != "chat" else None,
            "source": meta.provenance.source_line() if meta.provenance else None,
            "methodology": methodology,
            "section_order": [s.kind for s in self.sections],
            "integrity": self.integrity,
        }


# ── conversion helpers ───────────────────────────────────────────────────────

def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _measure(raw: Dict[str, Any], currency: str) -> Optional[Measure]:
    label = str(raw.get("label") or "").strip()
    if not label:
        return None
    unit = raw.get("unit") or infer_unit(label)
    value = to_decimal(raw.get("value"))
    if value is None:
        # Non-numeric KPI (a status, a period, a name): keep the text as shown.
        text = str(raw.get("formatted") or "").strip() or None
        return Measure(label=label, unit=unit, text=text)
    return Measure(label=label, value=value, unit=unit, currency=currency if unit == "money" else None)


def _chart(raw: Dict[str, Any]) -> tuple[Optional[ChartSection], str]:
    cats = [str(c) for c in raw.get("categories") or []]
    series = []
    for s in raw.get("series") or []:
        if not isinstance(s, dict):
            continue
        vals = [to_decimal(v) for v in (s.get("data") or s.get("values") or [])]
        # A short series means the source stopped early: pad with gaps, never zeros.
        vals = (vals + [None] * len(cats))[: len(cats)]
        series.append(Series(name=str(s.get("name") or "Value"), values=vals))
    y = str(raw.get("y_label") or "")
    named_units = {infer_unit(s.name) for s in series} - {"number"}
    if len(named_units) == 1:
        unit: Unit = named_units.pop()
    elif y.lower() == "count":
        unit = "count"
    elif y.upper() == "AED" or not y:
        unit = "money"
    else:
        unit = infer_unit(y)
    colored = raw if raw.get("series_colors") else theme.resolve_chart_colors(dict(raw))
    try:
        return ChartSection(
            chart_type=str(raw.get("chart_type") or "bar").lower(),
            title=str(raw.get("title") or ""),
            caption=raw.get("caption"),
            x_label=str(raw.get("x_label") or ""),
            y_label=y,
            unit=unit,
            categories=cats,
            series=series,
            series_colors=list(colored.get("series_colors") or []),
            point_colors=colored.get("point_colors"),
            requested_type=raw.get("requested_type"),
            downgrade_reason=raw.get("downgrade_reason"),
            takeaway=raw.get("takeaway"),
            total_indices=list(raw.get("total_indices") or []),
            ranked_subset=raw.get("ranked_subset"),
        ), ""
    except ValidationError as e:
        return None, _first_error(e)


def _table(raw: Dict[str, Any]) -> tuple[Optional[TableSection], str]:
    try:
        columns = [
            Column(
                key=str(c.get("key")),
                label=str(c.get("label") or c.get("key")),
                align=c.get("align") if c.get("align") in ("left", "right", "center") else "left",
            )
            for c in raw.get("columns") or []
            if isinstance(c, dict) and c.get("key")
        ]
        return TableSection(
            title=str(raw.get("title") or "Details"),
            columns=columns,
            rows=list(raw.get("rows") or []),
            raw_rows=list(raw.get("raw_rows") or []),
            total_rows=int(raw.get("total_rows") or len(raw.get("rows") or [])),
            truncated=bool(raw.get("truncated")),
            period=raw.get("period"),
            hide_total_note=bool(raw.get("hide_total_note")),
        ), ""
    except ValidationError as e:
        return None, _first_error(e)


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0] if e.errors() else {}
    msg = str(err.get("msg") or e)
    return msg.removeprefix("Value error, ")


def _chart_dict(c: ChartSection) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "chart_type": c.chart_type,
        "title": c.title,
        "caption": c.caption,
        "x_label": c.x_label,
        "y_label": c.y_label,
        "unit": c.unit,
        "categories": list(c.categories),
        "series": [{"name": s.name, "data": [_as_json_number(v) for v in s.values]} for s in c.series],
        "series_colors": list(c.series_colors),
    }
    if c.point_colors:
        out["point_colors"] = list(c.point_colors)
    if c.requested_type:
        out["requested_type"] = c.requested_type
    if c.downgrade_reason:
        out["downgrade_reason"] = c.downgrade_reason
    if c.takeaway:
        out["takeaway"] = c.takeaway
    if c.total_indices:
        out["total_indices"] = list(c.total_indices)
    if c.ranked_subset:
        out["ranked_subset"] = c.ranked_subset
    return out


def _methodology(meta: ReportMeta, spec: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    p = meta.provenance
    if p:
        lines.append(p.source_line())
        if p.endpoint:
            lines.append(f"Report reference: {p.endpoint}")
    if meta.period:
        lines.append(f"Period covered: {meta.period}")
    lines.append(f"Amounts in {meta.currency}. A dash (—) marks a value the source did not provide.")
    if meta.query:
        lines.append(f"Request: {meta.query.strip()[:200]}")
    return lines


def provenance_from_result(result: Dict[str, Any]) -> Optional[Provenance]:
    """Build Provenance from a runner result envelope (`data_source`, `notice`)."""
    if not isinstance(result, dict):
        return None
    ds = result.get("data_source") if isinstance(result.get("data_source"), dict) else {}
    notice = result.get("notice") if isinstance(result.get("notice"), dict) else {}
    routing = result.get("routing_info") if isinstance(result.get("routing_info"), dict) else {}
    if not ds and not notice and not routing:
        return None
    row_count = ds.get("row_count")
    return Provenance(
        tier=ds.get("tier"),
        endpoint=ds.get("endpoint") or routing.get("api_endpoint"),
        row_count=int(row_count) if isinstance(row_count, (int, float)) and not isinstance(row_count, bool) else None,
        truncated=bool(ds.get("truncated")),
        notice_code=notice.get("code"),
        notice_message=notice.get("message") if notice.get("code") else None,
    )


__all__: Sequence[str] = (
    "Measure", "Series", "Provenance", "ReportMeta", "KpiSection", "NarrativeSection",
    "ChartSection", "Column", "TableSection", "CalloutSection", "MethodologySection",
    "ReportDocument", "format_value", "infer_unit", "to_decimal", "provenance_from_result",
)
