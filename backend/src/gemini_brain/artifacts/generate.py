"""Branded file generators for ReportSpec → PDF/CSV/XLSX/DOCX/PPTX/MD."""
from __future__ import annotations

import concurrent.futures
import contextvars
import csv
import html
import io
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from gemini_brain.artifacts import theme
from gemini_brain.artifacts.report_spec import CANVAS_MAX_ROWS, XLSX_MAX_ROWS
from gemini_brain.artifacts.store import TTL_SECONDS, ArtifactRecord, put

logger = logging.getLogger("gemini_brain.artifacts.generate")

MIME = {
    "pdf": "application/pdf",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "md": "text/markdown",
}

_MONEY_HINTS = (
    "amount", "total", "balance", "revenue", "expense", "income", "tax", "net",
    "cashflow", "sales", "spend", "paid", "purchase", "outstanding", "due",
)
_COUNT_HINTS = ("count", "qty", "quantity")
_SHEET_NAME_FORBIDDEN = re.compile(r'[\[\]:*?/\\]')


#: Callout tones (ir.CalloutSection): a failed check must not look like a note.
_TONE_PREFIX = {"warning": "Warning: ", "caveat": "Note: ", "note": ""}
_TONE_FILL = {"warning": "#FDECEC", "caveat": "#FFF6E0", "note": theme.KPI_TINT}
_TONE_EDGE = {"warning": "#E34948", "caveat": "#EDA100", "note": theme.PRIMARY}


def _callouts(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(tone, text) pairs; older specs without `callouts` fall back to plain notes."""
    items = spec.get("callouts")
    if items:
        return [(c.get("tone") or "note", c.get("text") or "") for c in items if c.get("text")]
    return [("note", n) for n in spec.get("notes") or []]


def _callout_text(tone: str, text: str) -> str:
    return f"{_TONE_PREFIX.get(tone, '')}{text}"


def _slug(title: str, fmt: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "accutax-report")).strip("-").lower()
    base = (base or "accutax-report")[:48]
    return f"{base}.{fmt}"


def _header_bits(spec: Dict[str, Any]) -> Tuple[str, str, str]:
    title = spec.get("title") or "Accutax report"
    subtitle = spec.get("subtitle") or ""
    period = spec.get("period") or ""
    generated = spec.get("generated_at") or datetime.now(timezone.utc).isoformat()
    try:
        generated = datetime.fromisoformat(generated.replace("Z", "+00:00")).strftime("%d %b %Y %H:%M UTC")
    except ValueError:
        pass
    meta = " · ".join(p for p in (subtitle, period, f"Generated {generated}", "Confidential") if p)
    return title, subtitle, meta


def _all_tables(spec: Dict[str, Any], max_rows: int) -> List[Dict[str, Any]]:
    """Every table in the spec, row-capped. Replaces the old first-table-only helper.

    Caps the already-formatted `rows` ("AED 38,850.00") — PDF/DOCX/PPTX/MD just
    `str()` whatever's here, so it must stay display text, not raw numbers.
    `raw_rows` (real floats/ints, for XLSX cell values + number_format) is left
    untouched; render_xlsx reads it directly by its own key, not through here.
    """
    out = []
    for t in spec.get("tables") or []:
        table = dict(t)
        display_rows = table.get("rows") or []
        table["rows"] = display_rows[:max_rows]
        table["truncated"] = table.get("truncated") or len(display_rows) > max_rows
        out.append(table)
    return out


def _parse_narrative_lines(text: str) -> List[Tuple[str, str]]:
    """Line-based split of the narrator's own markdown subset (paragraphs,
    '-'/'*' bullets, '#'/'##' headings) into (kind, text) pairs.

    Not a general markdown parser — the narration prompt (claude_reasoner.py)
    deliberately restricts the model to this subset ("Write markdown: one
    short opening line, then bullets" / "Do not build a markdown table"), so
    tables/code/images never appear here and don't need handling.
    """
    out: List[Tuple[str, str]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append(("heading", line[level:].strip()))
        elif line.startswith(("- ", "* ")):
            out.append(("bullet", line[2:].strip()))
        else:
            out.append(("para", line))
    return out


def _escape_and_bold(text: str) -> str:
    """HTML-escape, then re-admit **bold** as reportlab's mini-markup <b>."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def _add_markdown_runs(paragraph: Any, text: str) -> None:
    """Split '**bold**' into separate bold/plain python-docx runs — python-docx
    has no markup parser, so each run's .bold has to be set individually."""
    pos = 0
    for m in re.finditer(r"\*\*(.+?)\*\*", text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        bold_run = paragraph.add_run(m.group(1))
        bold_run.bold = True
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def _excel_number_format(key: str, currency: str) -> Optional[str]:
    k = (key or "").lower()
    if any(h in k for h in _COUNT_HINTS) or "day" in k:
        return "#,##0"
    if any(h in k for h in _MONEY_HINTS):
        safe_currency = (currency or "AED").replace('"', "")
        return f'#,##0.00 "{safe_currency}"'
    return None


def _safe_sheet_name(name: str, used: set) -> str:
    clean = _SHEET_NAME_FORBIDDEN.sub(" ", (name or "Sheet").strip())[:31] or "Sheet"
    candidate = clean
    n = 2
    while candidate in used:
        suffix = f" {n}"
        candidate = clean[: 31 - len(suffix)] + suffix
        n += 1
    return candidate


def _human_axis_value(v: float, _pos: Any = None) -> str:
    """1,200,000 -> '1.2M', 800,000 -> '800K' — matches the live canvas's axis
    formatting instead of matplotlib's default scientific-notation offset
    ("1e6" printed once in a corner, unreadable in a static export)."""
    av = abs(v)
    if av >= 1_000_000:
        s = f"{v / 1_000_000:.1f}"
        return (s[:-2] if s.endswith(".0") else s) + "M"
    if av >= 10_000:
        return f"{v / 1_000:.0f}K"
    if av >= 1_000:
        s = f"{v / 1_000:.1f}"  # 1,500 must not read as "2K" beside a "2K" tick
        return (s[:-2] if s.endswith(".0") else s) + "K"
    return f"{v:.0f}"


def _value_label(v: float, *, signed: bool = False) -> str:
    """Bar/point label: the same compact form the takeaways use ("220.5K")."""
    if v != v:  # NaN: a gap has no label
        return ""
    from decimal import Decimal

    from gemini_brain.artifacts.ir import format_compact

    text = format_compact(Decimal(str(v)), "number")
    return f"+{text}" if signed and v > 0 else text


def _wrap(label: Any, width: int = 14) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(str(label), width)) or str(label)


#: One strict theme for every static chart (PDF/DOCX/MD): left-aligned bold
#: title, muted axes, hairline grid on the value axis only, no chart junk.
_CHART_STYLE = {
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.titlepad": 10,
    "axes.titlecolor": theme.TEXT_DARK,
    "axes.edgecolor": "#C3C2B7",
    "axes.labelcolor": theme.MUTED,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": theme.MUTED,
    "ytick.color": theme.MUTED,
    "legend.frameon": False,
    "legend.fontsize": 8,
}

#: Direct labels only while they stay readable; past this, the axis carries it.
_MAX_LABELLED_BARS = 15


def _value_axis(ax: Any, axis: str) -> None:
    from matplotlib.ticker import FuncFormatter

    target = ax.yaxis if axis == "y" else ax.xaxis
    target.set_major_formatter(FuncFormatter(_human_axis_value))
    ax.grid(axis=axis, color=theme.GRID_LINE, linewidth=0.6)
    ax.set_axisbelow(True)


def _category_axis(ax: Any, cats: List[str]) -> None:
    x = list(range(len(cats)))
    ax.set_xticks(x)
    long_labels = max((len(str(c)) for c in cats), default=0) > 10
    ax.set_xticklabels([_wrap(c) for c in cats], rotation=0 if not long_labels or len(cats) <= 6 else 30,
                       ha="center" if not long_labels or len(cats) <= 6 else "right")


def _label(ax: Any, rects: Any, values: List[float], labels: Optional[List[str]] = None) -> None:
    ax.bar_label(rects, labels=labels if labels is not None else [_value_label(v) for v in values],
                 padding=3, fontsize=7.5, color=theme.TEXT_DARK)


def _chart_colors(chart: Dict[str, Any]) -> Tuple[List[str], Optional[List[str]]]:
    """(series_colors, point_colors) as resolved on the spec. Charts built
    before theme.resolve_chart_colors existed are resolved here on the fly."""
    if not chart.get("series_colors"):
        chart = theme.resolve_chart_colors(dict(chart))
    return list(chart.get("series_colors") or [theme.PRIMARY]), chart.get("point_colors")


def _plot_values(data: List[Any], n: int) -> List[float]:
    """Numbers for matplotlib; a missing value is NaN (a gap), never 0."""
    out = [float("nan") if v is None else float(v) for v in (data or [])[:n]]
    return out + [float("nan")] * (n - len(out))


def _draw_pie(ax: Any, chart: Dict[str, Any], cats: List[str], series: List[Dict[str, Any]],
              series_colors: List[str], point_colors: Optional[List[str]], donut: bool) -> None:
    values = _plot_values(series[0].get("data"), len(cats))
    keep = [i for i, v in enumerate(values) if v == v and v > 0]
    total = sum(values[i] for i in keep)
    palette = point_colors or series_colors
    wedges, _texts, _auto = ax.pie(
        [values[i] for i in keep],
        colors=[palette[i % len(palette)] for i in keep],
        startangle=90,
        counterclock=False,
        autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
        pctdistance=0.78 if donut else 0.65,
        wedgeprops={"width": 0.42 if donut else 1, "edgecolor": "white", "linewidth": 1.5},
        textprops={"fontsize": 8, "color": theme.TEXT_DARK},
    )
    ax.legend(wedges, [f"{cats[i]} · {_value_label(values[i])}" for i in keep],
              loc="center left", bbox_to_anchor=(1.0, 0.5))
    if donut and total:
        ax.text(0, 0, _value_label(total), ha="center", va="center",
                fontsize=12, fontweight="bold", color=theme.TEXT_DARK)
    ax.set_aspect("equal")


def _waterfall_parts(values: List[float], totals: set) -> Tuple[List[float], List[float]]:
    """(invisible base, visible height) per bar — the same geometry for the
    matplotlib image and the native Excel/PowerPoint stacked-column bridge."""
    bottoms, heights, running = [], [], 0.0
    for i, v in enumerate(values):
        if i in totals:
            bottoms.append(0.0)
            heights.append(v)
            running = v
        else:
            bottoms.append(running if v >= 0 else running + v)
            heights.append(abs(v))
            running += v
    return bottoms, heights


def _draw_waterfall(ax: Any, chart: Dict[str, Any], cats: List[str], values: List[float],
                    point_colors: Optional[List[str]]) -> None:
    totals = set(chart.get("total_indices") or [0, len(values) - 1])
    bottoms, heights = _waterfall_parts(values, totals)
    x = list(range(len(cats)))
    colors = point_colors or [theme.PRIMARY] * len(cats)
    rects = ax.bar(x, heights, bottom=bottoms, color=colors, width=0.6)
    # Dashed connectors carry the running total from one bar to the next.
    level = 0.0
    for i, v in enumerate(values):
        level = v if i in totals else level + v
        if i < len(values) - 1:
            ax.plot([i + 0.3, i + 0.7], [level, level], color=theme.MUTED, linewidth=0.6, linestyle="--")
    # Totals read as amounts, steps as signed changes ("-52.3K").
    _label(ax, rects, values, [_value_label(v, signed=i not in totals) for i, v in enumerate(values)])
    _category_axis(ax, cats)
    _value_axis(ax, "y")
    ax.axhline(0, color="#C3C2B7", linewidth=0.8)


_RTL = re.compile(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")


def _shape_rtl(value: Any) -> Any:
    """Arabic/Hebrew text laid out for renderers that draw glyphs left to right.

    reportlab and matplotlib draw characters one by one, left to right, so
    Arabic comes out as isolated, reversed letters. When `arabic-reshaper` and
    `python-bidi` are installed, strings are joined and reordered here (Word,
    PowerPoint, Excel and the browser shape text themselves and need nothing).
    Without them, text passes through unchanged.
    """
    if isinstance(value, dict):
        return {k: _shape_rtl(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shape_rtl(v) for v in value]
    if not isinstance(value, str) or not _RTL.search(value):
        return value
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        return value
    return get_display(arabic_reshaper.reshape(value))


def _chart_png(chart: Dict[str, Any]) -> Optional[bytes]:
    """Render one chart (not the whole spec) to a matplotlib PNG.

    Every chart type the report model allows is drawn here with one theme.
    The takeaway is not baked into the image: renderers print it as text
    under the chart, so it stays selectable and searchable.
    Returns None (and logs) on any failure, so one unrenderable chart drops
    out of the file instead of failing the whole export.
    """
    chart = _shape_rtl(chart)
    cats = [str(c) for c in chart.get("categories") or []]
    series = chart.get("series") or []
    if not cats or not series:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        logger.warning("matplotlib unavailable for chart embed: %s", e)
        return None
    ctype = (chart.get("chart_type") or "bar").lower()
    height = max(2.8, min(7.0, 0.36 * len(cats) + 1.2)) if ctype == "hbar" else 3.6
    with plt.rc_context(_CHART_STYLE):
        fig, ax = plt.subplots(figsize=(8, height), dpi=150)
        try:
            n = len(cats)
            x = list(range(n))
            series_colors, point_colors = _chart_colors(chart)
            color_of = lambda i: series_colors[i] if i < len(series_colors) else theme.OTHER_COLOR  # noqa: E731
            if ctype in ("pie", "donut"):
                _draw_pie(ax, chart, cats, series, series_colors, point_colors, donut=ctype == "donut")
            elif ctype == "waterfall":
                _draw_waterfall(ax, chart, cats, _plot_values(series[0].get("data"), n), point_colors)
            elif ctype == "hbar":
                # Largest at the top: matplotlib draws y=0 at the bottom.
                y = list(range(n))[::-1]
                width = 0.8 / len(series)
                for i, s in enumerate(series):
                    data = _plot_values(s.get("data"), n)
                    offset = (i - (len(series) - 1) / 2) * width
                    colors = point_colors if (point_colors and len(series) == 1) else color_of(i)
                    rects = ax.barh([v - offset for v in y], data, height=width * 0.9, color=colors,
                                    label=s.get("name"))
                    if len(series) == 1 and n <= _MAX_LABELLED_BARS * 2:
                        _label(ax, rects, data)
                ax.set_yticks(y)
                ax.set_yticklabels([_wrap(c, 28) for c in cats])
                ax.tick_params(axis="y", length=0)
                _value_axis(ax, "x")
                ax.spines["left"].set_visible(False)
                if len(series) > 1:
                    ax.legend(loc="lower right")
            elif ctype == "stacked_bar":
                bottom = [0.0] * n
                for i, s in enumerate(series):
                    data = [0.0 if v != v else v for v in _plot_values(s.get("data"), n)]
                    ax.bar(x, data, bottom=bottom, color=color_of(i), width=0.6, label=s.get("name"),
                           edgecolor="white", linewidth=1)
                    bottom = [b + d for b, d in zip(bottom, data)]
                if n <= _MAX_LABELLED_BARS:
                    for xi, total in zip(x, bottom):
                        ax.text(xi, total, _value_label(total), ha="center", va="bottom", fontsize=7.5,
                                color=theme.TEXT_DARK)
                _category_axis(ax, cats)
                _value_axis(ax, "y")
                ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0))
            elif ctype in ("line", "area"):
                for i, s in enumerate(series):
                    data = _plot_values(s.get("data"), n)
                    if ctype == "area":
                        ax.fill_between(x, data, alpha=0.18, color=color_of(i), linewidth=0)
                    ax.plot(x, data, marker="o", markersize=4, linewidth=2, label=s.get("name"), color=color_of(i))
                    last = max((j for j, v in enumerate(data) if v == v), default=None)
                    if last is not None:
                        ax.annotate(_value_label(data[last]), (last, data[last]), textcoords="offset points",
                                    xytext=(0, 7), ha="center", fontsize=7.5, color=theme.TEXT_DARK)
                _category_axis(ax, cats)
                _value_axis(ax, "y")
                if len(series) > 1:
                    ax.legend()
            else:  # bar (single or grouped)
                width = 0.8 / len(series) if len(series) > 1 else 0.6
                label_each = n * len(series) <= _MAX_LABELLED_BARS
                for i, s in enumerate(series):
                    data = _plot_values(s.get("data"), n)
                    offset = (i - (len(series) - 1) / 2) * width if len(series) > 1 else 0
                    colors = point_colors if (point_colors and len(series) == 1) else color_of(i)
                    rects = ax.bar([v + offset for v in x], data, width=width, color=colors, label=s.get("name"))
                    if label_each:
                        _label(ax, rects, data)
                _category_axis(ax, cats)
                _value_axis(ax, "y")
                if len(series) > 1:
                    ax.legend()
            if chart.get("y_label") and ctype not in ("pie", "donut", "hbar"):
                ax.set_ylabel(chart["y_label"])
            ax.set_title(chart.get("title") or "")
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png")
            return buf.getvalue()
        except Exception as e:
            logger.warning("Chart %r skipped in export: %s", chart.get("title"), e)
            return None
        finally:
            plt.close(fig)


def render_csv(spec: Dict[str, Any]) -> bytes:
    # Deliberately bypasses _all_tables: CSV wants raw, machine-parseable
    # numbers (38850, not "AED 38,850.00") for spreadsheet import, same as
    # XLSX — not the print-formatted display strings every other format uses.
    tables = spec.get("tables") or []
    table = tables[0] if tables else None
    buf = io.StringIO()
    if not table:
        buf.write("metric,value\n")
        for kpi in spec.get("kpis") or []:
            buf.write(f"{kpi.get('label')},{kpi.get('formatted') or kpi.get('value')}\n")
        return ("﻿" + buf.getvalue()).encode("utf-8")
    columns = table.get("columns") or []
    rows = (table.get("raw_rows") or table.get("rows") or [])[:XLSX_MAX_ROWS]
    keys = [c.get("key") for c in columns] or (list(rows[0].keys()) if rows else [])
    writer = csv.writer(buf)
    writer.writerow([c.get("label") or k for c, k in zip(columns, keys)] or keys)
    for row in rows:
        writer.writerow([row.get(k, "") for k in keys])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def render_xlsx(spec: Dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.chart import AreaChart, BarChart, DoughnutChart, LineChart, PieChart, Reference
    from openpyxl.chart.label import DataLabelList
    from openpyxl.chart.marker import DataPoint
    from openpyxl.worksheet.table import Table as ExcelTable, TableStyleInfo

    from gemini_brain.artifacts.ir import infer_unit

    wb = Workbook()
    title, _, meta = _header_bits(spec)
    currency = spec.get("currency") or "AED"
    header_fill = PatternFill("solid", fgColor=theme.bare(theme.PRIMARY))
    header_font = Font(color=theme.bare(theme.WHITE), bold=True)
    zebra_fill = PatternFill("solid", fgColor=theme.bare(theme.ZEBRA_TINT))
    thin = Border(
        left=Side(style="thin", color=theme.bare(theme.GRID_LINE)),
        right=Side(style="thin", color=theme.bare(theme.GRID_LINE)),
        top=Side(style="thin", color=theme.bare(theme.GRID_LINE)),
        bottom=Side(style="thin", color=theme.bare(theme.GRID_LINE)),
    )

    def _native_chart(ws, chart_spec: Dict[str, Any], start_row: int) -> int:
        """Write a small backing table + a native chart object. Returns rows used."""
        cats = chart_spec.get("categories") or []
        series = chart_spec.get("series") or []
        if not cats or not series:
            return 0
        ctype = (chart_spec.get("chart_type") or "bar").lower()
        header_row = start_row
        if chart_spec.get("takeaway"):
            note = ws.cell(header_row, 1, chart_spec["takeaway"])
            note.font = Font(italic=True, size=9, color=theme.bare(theme.MUTED))
            header_row += 1
        head_cell = ws.cell(header_row, 1, chart_spec.get("x_label") or "Category")
        head_cell.font, head_cell.fill, head_cell.border = header_font, header_fill, thin
        for s_i, s in enumerate(series, start=2):
            c = ws.cell(header_row, s_i, s.get("name") or f"Series {s_i - 1}")
            c.font, c.fill, c.border = header_font, header_fill, thin
        for r_i, cat in enumerate(cats, start=header_row + 1):
            ws.cell(r_i, 1, str(cat)).border = thin
        for s_i, s in enumerate(series, start=2):
            data = s.get("data") or []
            for r_i, val in enumerate(data[: len(cats)], start=header_row + 1):
                ws.cell(r_i, s_i, val).border = thin
        last_row = header_row + len(cats)
        last_col = 1 + len(series)
        data_min_col, data_max_col = 2, last_col

        if ctype == "waterfall":
            # Excel has no bridge chart openpyxl can write: draw it as stacked
            # columns over an invisible base, from helper columns beside the table.
            values = [float(v) if v is not None else 0.0 for v in (series[0].get("data") or [])][: len(cats)]
            bases, heights = _waterfall_parts(values, set(chart_spec.get("total_indices") or []))
            for col, name, vals in ((last_col + 1, "Base (chart helper)", bases), (last_col + 2, "Bar (chart helper)", heights)):
                ws.cell(header_row, col, name).font = Font(size=8, color=theme.bare(theme.MUTED))
                for r_i, val in enumerate(vals, start=header_row + 1):
                    ws.cell(r_i, col, val).font = Font(size=8, color=theme.bare(theme.MUTED))
            data_min_col, data_max_col = last_col + 1, last_col + 2
            last_col += 2

        chart_cls = {"line": LineChart, "area": AreaChart, "pie": PieChart, "donut": DoughnutChart}.get(ctype, BarChart)
        chart = chart_cls()
        if isinstance(chart, BarChart):
            chart.type = "bar" if ctype == "hbar" else "col"
            if ctype in ("stacked_bar", "waterfall"):
                chart.grouping = "stacked"
                chart.overlap = 100
            if ctype == "hbar":
                chart.x_axis.scaling.orientation = "maxMin"  # first (largest) category on top
        if isinstance(chart, DoughnutChart):
            chart.holeSize = 55
        chart.title = chart_spec.get("title") or None
        chart.height, chart.width = 9, 18
        data_ref = Reference(ws, min_col=data_min_col, max_col=data_max_col, min_row=header_row, max_row=last_row)
        cats_ref = Reference(ws, min_col=1, min_row=header_row + 1, max_row=last_row)
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
        if len(series) == 1 or ctype == "waterfall":
            chart.legend = None if ctype not in ("pie", "donut") else chart.legend
        # Same resolved colors as the canvas and the PDF, not Excel's theme.
        series_colors, point_colors = _chart_colors(chart_spec)
        if ctype == "waterfall":
            base_series, bar_series = chart.series[0], chart.series[1]
            base_series.graphicalProperties.noFill = True
            base_series.graphicalProperties.line.noFill = True
            for p_i, p_color in enumerate((point_colors or [])[: len(cats)]):
                point = DataPoint(idx=p_i)
                point.graphicalProperties.solidFill = theme.bare(p_color)
                bar_series.dPt.append(point)
            bar_series.dLbls = DataLabelList()
            bar_series.dLbls.showVal = True
            ws.add_chart(chart, f"{get_column_letter(last_col + 2)}{start_row}")
            return last_row - start_row + 1
        if ctype in ("bar", "hbar") and len(series) == 1 and len(cats) <= 15:
            chart.dataLabels = DataLabelList()
            chart.dataLabels.showVal = True
        for s_i, xl_series in enumerate(chart.series):
            color = theme.bare(series_colors[s_i] if s_i < len(series_colors) else theme.OTHER_COLOR)
            if ctype == "line":
                xl_series.graphicalProperties.line.solidFill = color
                xl_series.graphicalProperties.line.width = 28575  # 2.25pt in EMU
                xl_series.smooth = False
            else:
                xl_series.graphicalProperties.solidFill = color
                xl_series.graphicalProperties.line.solidFill = color
            if point_colors and s_i == 0:
                for p_i, p_color in enumerate(point_colors[: len(cats)]):
                    point = DataPoint(idx=p_i)
                    point.graphicalProperties.solidFill = theme.bare(p_color)
                    point.graphicalProperties.line.solidFill = theme.bare(theme.WHITE)
                    xl_series.dPt.append(point)
        if ctype in ("pie", "donut"):
            chart.dataLabels = DataLabelList()
            chart.dataLabels.showPercent = True
        ws.add_chart(chart, f"{get_column_letter(last_col + 2)}{start_row}")
        return last_row - start_row + 1

    summary = wb.active
    summary.title = "Summary"
    # A custom-bordered block reads as "designed" only without Excel's own
    # default gridlines fighting it underneath — same reasoning on every
    # sheet created below.
    summary.sheet_view.showGridLines = False
    summary["A1"] = theme.BRAND_NAME
    summary["A1"].font = Font(bold=True, color=theme.bare(theme.PRIMARY), size=16)
    summary["A2"] = title
    summary["A2"].font = Font(bold=True, size=14)
    summary["A3"] = meta
    summary["A3"].font = Font(size=9, color=theme.bare(theme.MUTED))
    divider = Border(bottom=Side(style="thin", color=theme.bare(theme.GRID_LINE)))
    summary["A3"].border = divider
    summary["B3"].border = divider
    row_i = 5
    summary["A4"] = "Metric"
    summary["B4"] = "Value"
    for cell in (summary["A4"], summary["B4"]):
        cell.fill = header_fill
        cell.font = header_font
        cell.border = thin
    for kpi in spec.get("kpis") or []:
        summary[f"A{row_i}"] = kpi.get("label")
        val_cell = summary[f"B{row_i}"]
        value, fmt = _xlsx_value(kpi.get("value"), kpi.get("unit") or infer_unit(str(kpi.get("label") or "")), currency)
        if kpi.get("value") is not None:
            val_cell.value = value
            if fmt:
                val_cell.number_format = fmt
        else:
            val_cell.value = kpi.get("formatted")
        val_cell.alignment = Alignment(horizontal="right")
        summary[f"A{row_i}"].border = thin
        val_cell.border = thin
        if row_i % 2 == 0:
            summary[f"A{row_i}"].fill = zebra_fill
            val_cell.fill = zebra_fill
        row_i += 1

    wrap = Alignment(wrap_text=True, vertical="top")

    def _block(heading: str, lines: List[Tuple[str, Optional[Font]]]) -> None:
        """A titled block of full-width text rows (merged A:D so text can wrap)."""
        nonlocal row_i
        if not lines:
            return
        row_i += 1
        summary[f"A{row_i}"] = heading
        summary[f"A{row_i}"].font = Font(bold=True, size=11, color=theme.bare(theme.TEXT_DARK))
        row_i += 1
        for text, font in lines:
            summary.merge_cells(start_row=row_i, start_column=1, end_row=row_i, end_column=4)
            cell = summary.cell(row_i, 1, text)
            cell.alignment = wrap
            if font is not None:
                cell.font = font
            summary.row_dimensions[row_i].height = max(15, 15 * (len(text) // 95 + 1))
            row_i += 1

    parts = spec.get("narrative_parts")
    if parts:
        lines: List[Tuple[str, Optional[Font]]] = []
        if parts.get("headline"):
            lines.append((parts["headline"], Font(bold=True, size=12)))
        if parts.get("summary"):
            lines.append((" ".join(parts["summary"]), None))
        for label, key in (("Key drivers", "drivers"), ("Risks and watch-points", "risks"),
                           ("Recommended actions", "actions")):
            if parts.get(key):
                lines.append((label.upper(), Font(bold=True, size=9, color=theme.bare(theme.PRIMARY))))
                lines += [(f"•  {item}", None) for item in parts[key]]
        _block("Executive summary", lines)
    elif spec.get("narrative"):
        _block("Executive summary", [(re.sub(r"\*\*(.+?)\*\*", r"\1", t), None)
                                     for _, t in _parse_narrative_lines(spec["narrative"])])

    _block("Notes", [
        (_callout_text(tone, text),
         Font(bold=True, color=theme.bare(_TONE_EDGE["warning"])) if tone == "warning" else None)
        for tone, text in _callouts(spec)
    ])
    _block("About this report", [(line, Font(size=9, color=theme.bare(theme.MUTED)))
                                 for line in spec.get("methodology") or []])
    summary.column_dimensions["A"].width = 34
    summary.column_dimensions["B"].width = 24
    summary.column_dimensions["C"].width = 24
    summary.column_dimensions["D"].width = 24

    tables = _all_tables(spec, XLSX_MAX_ROWS)
    used_names = {"Summary"}
    for idx, table in enumerate(tables):
        raw_title = table.get("title") or (f"Data {idx + 1}" if len(tables) > 1 else "Data")
        sheet_name = _safe_sheet_name(raw_title, used_names)
        used_names.add(sheet_name)
        data_ws = wb.create_sheet(sheet_name)
        data_ws.sheet_view.showGridLines = False
        columns = table.get("columns") or []
        keys = [c.get("key") for c in columns]
        if not keys and table.get("raw_rows"):
            keys = list(table["raw_rows"][0].keys())
            columns = [{"key": k, "label": k.replace("_", " ").title()} for k in keys]
        headers = _unique_headers([str(c.get("label") or c.get("key")) for c in columns])
        for col_i, header in enumerate(headers, start=1):
            cell = data_ws.cell(1, col_i, header)
            cell.fill = header_fill
            cell.font = header_font
            cell.border = thin
            cell.alignment = Alignment(horizontal="right" if columns[col_i - 1].get("align") == "right" else "left")
        source_rows = (table.get("raw_rows") or table.get("rows") or [])[:XLSX_MAX_ROWS]
        widths = [len(h) for h in headers]
        units = [infer_unit(str(c.get("key") or "")) for c in columns]
        money_cols = set()
        has_total_rows = False
        for r_i, row in enumerate(source_rows, start=2):
            first = str(row.get(keys[0]) if keys else "")
            is_total = bool(_TOTAL_ROW.match(first))
            has_total_rows |= is_total
            for c_i, col in enumerate(columns, start=1):
                value, fmt = _xlsx_value(row.get(col.get("key")), units[c_i - 1], currency,
                                         key=str(col.get("key") or ""))
                cell = data_ws.cell(r_i, c_i, value)
                cell.border = thin
                cell.alignment = Alignment(horizontal=col.get("align") or ("right" if fmt else "left"))
                if fmt:
                    cell.number_format = fmt
                if units[c_i - 1] == "money" and isinstance(value, (int, float)):
                    money_cols.add(c_i)
                if is_total:
                    cell.font = Font(bold=True)
                elif r_i % 2 == 0:
                    cell.fill = zebra_fill
                shown = f"{value:,.2f}" if isinstance(value, float) else str(value if value is not None else "")
                widths[c_i - 1] = max(widths[c_i - 1], len(shown) + (4 if units[c_i - 1] == "money" else 0))
        last = len(source_rows) + 1
        if source_rows and columns:
            # A real Excel table: filter buttons, structured references, sorting.
            ref = f"A1:{get_column_letter(len(columns))}{last}"
            xl_table = ExcelTable(displayName=_table_name(sheet_name, idx), ref=ref)
            xl_table.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
            data_ws.add_table(xl_table)
            # Totals as live formulas (SUBTOTAL respects filters), unless the
            # table already carries its own total rows (a P&L's line items).
            if money_cols and not has_total_rows and 1 not in money_cols:
                total_row = last + 1
                label = data_ws.cell(total_row, 1, "Total")
                label.font = Font(bold=True)
                for c_i in sorted(money_cols):
                    letter = get_column_letter(c_i)
                    cell = data_ws.cell(total_row, c_i, f"=SUBTOTAL(109,{letter}2:{letter}{last})")
                    cell.font = Font(bold=True)
                    cell.number_format = _xlsx_value(0.0, "money", currency)[1]
                    cell.alignment = Alignment(horizontal="right")
                for c_i in range(1, len(columns) + 1):
                    data_ws.cell(total_row, c_i).border = Border(top=Side(style="medium", color=theme.bare(theme.TEXT_DARK)))
        data_ws.freeze_panes = "A2"
        for col_i, w in enumerate(widths, start=1):
            data_ws.column_dimensions[get_column_letter(col_i)].width = max(10, min(50, w + 3))

    charts = spec.get("charts") or []
    if charts:
        chart_ws = wb.create_sheet(_safe_sheet_name("Charts", used_names))
        chart_ws.sheet_view.showGridLines = False
        cursor = 1
        for chart_spec in charts:
            used = _native_chart(chart_ws, chart_spec, cursor)
            cursor += max(used, 16) + 4

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$")
_PERCENT_TEXT = re.compile(r"^-?\d+(?:\.\d+)?%$")
_NUMBER_TEXT = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


def _xlsx_value(value: Any, unit: str, currency: str, *, key: str = "") -> Tuple[Any, Optional[str]]:
    """(cell value, number format) — real numbers and dates, never numbers as text.

    "12.3%" becomes 0.123 formatted as a percentage; a percent KPI (34.2)
    becomes 0.342; an ISO date becomes a date; "1,234.50" becomes 1234.5.
    """
    import datetime as _dt

    safe_currency = (currency or "AED").replace('"', "")
    money = f'#,##0.00 "{safe_currency}";-#,##0.00 "{safe_currency}"'
    if isinstance(value, str):
        text = value.strip()
        if _PERCENT_TEXT.match(text):
            return float(text[:-1]) / 100, "0.0%"
        if _ISO_DATE.match(text) and ("date" in key.lower() or len(text) == 10):
            try:
                return _dt.date.fromisoformat(text[:10]), "dd mmm yyyy"
            except ValueError:
                return value, None
        if _NUMBER_TEXT.match(text) and unit in ("money", "count", "days"):
            value = float(text.replace(",", ""))
        else:
            return value, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value, None
    if unit == "money":
        return value, money
    if unit in ("count", "days"):
        return value, "#,##0"
    if unit == "percent":
        return value / 100, "0.0%"
    return value, None


def _unique_headers(headers: List[str]) -> List[str]:
    """Excel tables need unique, non-empty column names."""
    seen: Dict[str, int] = {}
    out = []
    for h in headers:
        base = h.strip() or "Column"
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base} {n + 1}")
    return out


def _table_name(sheet: str, idx: int) -> str:
    """A valid Excel table name: letters, digits, underscores; starts with a letter."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", sheet).strip("_") or "Table"
    if not name[0].isalpha():
        name = f"T_{name}"
    return f"{name[:200]}_{idx + 1}"


_PDF_FONTS: Optional[Tuple[str, str, str]] = None


def _pdf_fonts() -> Tuple[str, str, str]:
    """(regular, bold, italic) font names for the PDF.

    DejaVu Sans ships with matplotlib (already a dependency), covers Arabic,
    Latin extended and currency symbols such as ₹, and has same-width digits
    so number columns line up. Falls back to Helvetica if it cannot load.
    """
    global _PDF_FONTS
    if _PDF_FONTS is not None:
        return _PDF_FONTS
    try:
        import os

        import matplotlib
        from reportlab.lib.fonts import addMapping
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
        for name, file in (("ReportSans", "DejaVuSans.ttf"), ("ReportSans-Bold", "DejaVuSans-Bold.ttf"),
                           ("ReportSans-Italic", "DejaVuSans-Oblique.ttf"),
                           ("ReportSans-BoldItalic", "DejaVuSans-BoldOblique.ttf")):
            pdfmetrics.registerFont(TTFont(name, os.path.join(base, file)))
        addMapping("ReportSans", 0, 0, "ReportSans")
        addMapping("ReportSans", 1, 0, "ReportSans-Bold")
        addMapping("ReportSans", 0, 1, "ReportSans-Italic")
        addMapping("ReportSans", 1, 1, "ReportSans-BoldItalic")
        _PDF_FONTS = ("ReportSans", "ReportSans-Bold", "ReportSans-Italic")
    except Exception as e:  # pragma: no cover - depends on the host's matplotlib install
        logger.warning("PDF Unicode font unavailable, using Helvetica: %s", e)
        _PDF_FONTS = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")
    return _PDF_FONTS


#: Rows whose first cell names a total are drawn as totals (bold, rule above).
_TOTAL_ROW = re.compile(r"^(?:total\b|net\s+(?:profit|income|loss)\b|grand total\b)", re.IGNORECASE)


def _column_widths(rows: List[List[str]], font: str, size: float, available: float) -> List[float]:
    """Column widths that fill the page without breaking short values.

    Numbers, dates, codes and statuses (short cells) keep their full width and
    never wrap; only long text columns (names, descriptions) give up width, and
    never below their longest word. Spare width goes to the text columns.
    Measured in the bold face, since headers and total rows are bold.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    padding = 12.0
    width_of = lambda text: stringWidth(text, font, size) + padding  # noqa: E731
    sample = rows[:200]
    natural, text_floor, header_floor, wraps = [], [], [], []
    for c in range(len(rows[0])):
        header, cells = str(sample[0][c]), [str(r[c]) for r in sample[1:]]
        data = max((width_of(t) for t in cells), default=padding)
        head_word = max((width_of(w) for w in header.split()), default=padding)
        long_text = max((len(t) for t in cells), default=0) > 18  # names, descriptions
        natural.append(max(data, width_of(header)))
        # Long text may wrap down to its longest word; anything short keeps its data width.
        word = max((width_of(w) for t in cells for w in t.split()), default=padding)
        text_floor.append(max(word, head_word) if long_text else max(data, width_of(header)))
        header_floor.append(max(word if long_text else data, head_word))  # only the header wraps
        wraps.append(long_text)

    total = sum(natural)
    if total <= available:
        extra = available - total
        grow = [i for i, w in enumerate(wraps) if w] or list(range(len(natural)))
        return [w + (extra / len(grow) if i in grow else 0) for i, w in enumerate(natural)]

    widths = list(natural)
    # Give up width in order of least harm: long text first, then header lines,
    # and only then (a table that simply has too many columns) everything evenly.
    for floor in (text_floor, header_floor):
        over = sum(widths) - available
        slack = [max(0.0, w - f) for w, f in zip(widths, floor)]
        capacity = sum(slack)
        if over <= 0 or capacity <= 0:
            continue
        take = min(over, capacity)
        widths = [w - take * s / capacity for w, s in zip(widths, slack)]
    if sum(widths) > available:
        scale = available / sum(widths)
        widths = [w * scale for w in widths]
    return widths


def render_pdf(spec: Dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.platypus import (
        HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    regular, bold, italic = _pdf_fonts()
    spec = _shape_rtl(spec)  # joined, right-to-left Arabic where the libraries allow
    ink, muted_c, primary = colors.HexColor(theme.TEXT_DARK), colors.HexColor(theme.MUTED), colors.HexColor(theme.PRIMARY)
    title, _, meta = _header_bits(spec)
    page_w, page_h = A4
    margin = 18 * mm
    width = page_w - 2 * margin
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin,
                            topMargin=22 * mm, bottomMargin=20 * mm, title=title, author=theme.BRAND_NAME)

    def style(name: str, **kw: Any) -> ParagraphStyle:
        base = dict(fontName=regular, fontSize=9.5, leading=13.5, textColor=ink)
        base.update(kw)
        return ParagraphStyle(name, **base)

    brand = style("Brand", fontName=bold, fontSize=9, textColor=primary, leading=11, spaceAfter=4)
    h1 = style("DocTitle", fontName=bold, fontSize=20, leading=24, spaceAfter=4)
    meta_style = style("Meta", fontSize=8.5, textColor=muted_c, spaceAfter=8)
    h2 = style("Section", fontName=bold, fontSize=12, leading=15, spaceBefore=10, spaceAfter=6, keepWithNext=1)
    h3 = style("Sub", fontName=bold, fontSize=8, leading=10, textColor=primary, spaceBefore=8, spaceAfter=3,
               keepWithNext=1)
    body = style("Body", spaceAfter=5)
    headline = style("Headline", fontName=bold, fontSize=12, leading=16, spaceAfter=6)
    bullet = style("Bullet", leftIndent=12, bulletIndent=2, spaceAfter=3)
    small = style("Small", fontSize=8, leading=11, textColor=muted_c)
    # Regular face, not italic: DejaVu's oblique face has no Arabic glyphs, and
    # a takeaway naming an Arabic customer rendered as empty boxes.
    takeaway_style = style("Takeaway", fontSize=9, leading=12, textColor=muted_c)
    note_style = style("Note", fontSize=8.5, leading=12)
    cell = style("Cell", fontSize=8, leading=10)
    cell_right = style("CellR", fontSize=8, leading=10, alignment=TA_RIGHT)
    head_cell = style("Head", fontName=bold, fontSize=8, leading=10, textColor=colors.white)
    head_cell_right = style("HeadR", fontName=bold, fontSize=8, leading=10, textColor=colors.white, alignment=TA_RIGHT)
    kpi_label = style("KpiLabel", fontSize=7.5, leading=10, textColor=muted_c)
    rule = lambda: HRFlowable(width="100%", thickness=0.6, color=colors.HexColor(theme.GRID_LINE), spaceAfter=8)  # noqa: E731

    story: List[Any] = [Paragraph(theme.BRAND_NAME, brand), Paragraph(html.escape(title), h1),
                        Paragraph(html.escape(meta), meta_style), rule()]

    # ── KPI cards: every KPI, four to a row ──────────────────────────────────
    kpis = spec.get("kpis") or []
    for start in range(0, len(kpis), 4):
        row = kpis[start:start + 4]
        cells = []
        card_w = width / 4
        for k in row:
            value = str(k.get("formatted") or k.get("value") or "")
            # Largest size (up to 15pt) at which the value fits the card on one line.
            fit = (card_w - 18) / max(stringWidth(value, bold, 1), 1)
            size = max(8.5, min(15.0, fit))
            value_style = style("KpiValue", fontName=bold, fontSize=size, leading=size + 3)
            cells.append([Paragraph(html.escape(str(k.get("label") or "")).upper(), kpi_label),
                          Paragraph(html.escape(value), value_style)])
        grid = Table([cells], colWidths=[card_w] * len(row), hAlign="LEFT")
        grid.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(theme.KPI_TINT)),
            ("LINEBEFORE", (1, 0), (-1, -1), 2, colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story += [grid, Spacer(1, 4)]
    if kpis:
        story.append(Spacer(1, 6))

    # ── executive summary ────────────────────────────────────────────────────
    parts = spec.get("narrative_parts")
    narrative = spec.get("narrative")
    if parts or narrative:
        story.append(Paragraph("Executive summary", h2))
        if parts:
            if parts.get("headline"):
                story.append(Paragraph(_escape_and_bold(parts["headline"]), headline))
            if parts.get("summary"):
                story.append(Paragraph(_escape_and_bold(" ".join(parts["summary"])), body))
            for label, key in (("KEY DRIVERS", "drivers"), ("RISKS AND WATCH-POINTS", "risks"),
                               ("RECOMMENDED ACTIONS", "actions")):
                items = parts.get(key) or []
                if items:
                    story.append(Paragraph(label, h3))
                    story += [Paragraph(_escape_and_bold(i), bullet, bulletText="•") for i in items]
        else:
            for kind, text in _parse_narrative_lines(narrative):
                html_text = _escape_and_bold(text)
                if kind == "heading":
                    story.append(Paragraph(html_text.upper(), h3))
                elif kind == "bullet":
                    story.append(Paragraph(html_text, bullet, bulletText="•"))
                else:
                    story.append(Paragraph(html_text, body))
        story.append(Spacer(1, 6))

    # ── charts: image + takeaway, kept together ─────────────────────────────
    for chart in spec.get("charts") or []:
        png = _chart_png(chart)
        if not png:
            continue
        iw, ih = ImageReader(io.BytesIO(png)).getSize()
        block: List[Any] = [Image(io.BytesIO(png), width=width, height=width * ih / iw)]
        if chart.get("takeaway"):
            block.append(Paragraph(html.escape(chart["takeaway"]), takeaway_style))
        story += [KeepTogether(block), Spacer(1, 12)]

    # ── tables: sized to content, split across pages with a repeated header ──
    for table in _all_tables(spec, CANVAS_MAX_ROWS):
        columns = table.get("columns") or []
        rows = table.get("rows") or []
        if not columns or not rows:
            continue
        keys = [c.get("key") for c in columns]
        right = [c.get("align") == "right" for c in columns]
        text_rows = [[str(c.get("label") or c.get("key")) for c in columns]]
        text_rows += [[str(r.get(k, "") if r.get(k) is not None else "") for k in keys] for r in rows]
        widths = _column_widths(text_rows, bold, 8, width)
        data = [[Paragraph(html.escape(t), head_cell_right if right[i] else head_cell) for i, t in enumerate(text_rows[0])]]
        totals = []
        for r_i, r in enumerate(text_rows[1:], start=1):
            is_total = bool(_TOTAL_ROW.match(r[0] or ""))
            if is_total:
                totals.append(r_i)
            data.append([
                Paragraph(f"<b>{html.escape(t)}</b>" if is_total else html.escape(t), cell_right if right[i] else cell)
                for i, t in enumerate(r)
            ])
        tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
        commands = [
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(theme.ZEBRA_TINT)]),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor(theme.GRID_LINE)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for r_i in totals:
            commands.append(("LINEABOVE", (0, r_i), (-1, r_i), 0.8, ink))
        tbl.setStyle(TableStyle(commands))
        story += [Paragraph(html.escape(table.get("title") or "Details"), h2), tbl]
        total_rows = table.get("total_rows") or len(rows)
        if total_rows > len(rows) and not table.get("hide_total_note"):
            story.append(Paragraph(
                f"Showing {len(rows):,} of {total_rows:,} rows. The Excel and CSV files include every row.", small))
        story.append(Spacer(1, 10))

    # ── notes, warnings, caveats ────────────────────────────────────────────
    for tone, text in _callouts(spec):
        label = _TONE_PREFIX.get(tone, "").rstrip(": ")
        body_text = html.escape(text, quote=False)
        box = Table([[Paragraph(f"<b>{label}.</b> {body_text}" if label else body_text, note_style)]], colWidths=[width])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(_TONE_FILL.get(tone, theme.KPI_TINT))),
            ("LINEBEFORE", (0, 0), (0, -1), 2.5, colors.HexColor(_TONE_EDGE.get(tone, theme.PRIMARY))),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story += [box, Spacer(1, 4)]

    methodology = spec.get("methodology") or []
    if methodology:
        about: List[Any] = [Spacer(1, 8), Paragraph("About this report", h2)]
        about += [Paragraph(html.escape(line, quote=False), small) for line in methodology]
        story.append(KeepTogether(about))

    class _NumberedCanvas(pdf_canvas.Canvas):
        """Draws the running header and "Page X of Y" once the page count is known."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._pages: List[Dict[str, Any]] = []

        def showPage(self) -> None:  # noqa: N802 - reportlab API
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            count = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(count)
                super().showPage()
            super().save()

        def _decorate(self, count: int) -> None:
            self.saveState()
            self.setFont(regular, 7.5)
            self.setFillColor(muted_c)
            self.setStrokeColor(colors.HexColor(theme.GRID_LINE))
            self.setLineWidth(0.5)
            if self._pageNumber > 1:  # page 1 carries the full title block instead
                self.drawString(margin, page_h - 13 * mm, f"{theme.BRAND_NAME} · {title}"[:110])
                self.line(margin, page_h - 15 * mm, page_w - margin, page_h - 15 * mm)
            self.line(margin, 14 * mm, page_w - margin, 14 * mm)
            self.drawString(margin, 10 * mm, f"{theme.BRAND_NAME} · Confidential")
            self.drawRightString(page_w - margin, 10 * mm, f"Page {self._pageNumber} of {count}")
            self.restoreState()

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()


def render_docx(spec: Dict[str, Any]) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def _shade(cell, hex_color: str) -> None:
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hex_color)
        cell._tc.get_or_add_tcPr().append(shd)

    def _repeat_header(row) -> None:
        trPr = row._tr.get_or_add_trPr()
        el = OxmlElement("w:tblHeader")
        el.set(qn("w:val"), "true")
        trPr.append(el)

    def _field(paragraph, instr: str) -> None:
        # Word computes PAGE/NUMPAGES on open/print (F9 to force it manually) —
        # this writes the field code, not a cached value, so it starts blank.
        run = paragraph.add_run()
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instr_el = OxmlElement("w:instrText")
        instr_el.set(qn("xml:space"), "preserve")
        instr_el.text = instr
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        run._r.append(begin)
        run._r.append(instr_el)
        run._r.append(end)

    def _borders(table, hex_color: str) -> None:
        """Hairline borders in the grid color instead of Word's black "Table Grid"."""
        tbl_pr = table._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:val"), "single" if edge in ("top", "bottom", "insideH") else "nil")
            el.set(qn("w:sz"), "4")
            el.set(qn("w:color"), hex_color)
            borders.append(el)
        tbl_pr.append(borders)

    def _style_run(run, *, size: float = None, bold: bool = None, italic: bool = None, color: str = None) -> None:
        if size is not None:
            run.font.size = Pt(size)
        if bold is not None:
            run.bold = bold
        if italic is not None:
            run.italic = italic
        if color is not None:
            run.font.color.rgb = RGBColor.from_string(theme.bare(color))

    title, _, meta = _header_bits(spec)
    doc = Document()
    from docx.shared import Cm

    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)  # A4, like the PDF
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(2.0))
    # One typeface and one ink color for the whole document.
    for name in ("Normal", "Heading 1", "Heading 2", "Heading 3", "List Bullet", "Title"):
        st = doc.styles[name]
        st.font.name = "Calibri"
        st.font.color.rgb = RGBColor.from_string(theme.bare(theme.TEXT_DARK))
    doc.styles["Normal"].font.size = Pt(10)

    brand = doc.add_paragraph()
    _style_run(brand.add_run(theme.BRAND_NAME), size=9, bold=True, color=theme.PRIMARY)
    heading = doc.add_paragraph()
    _style_run(heading.add_run(title), size=20, bold=True)
    p = doc.add_paragraph()
    _style_run(p.add_run(meta), size=8.5, color=theme.MUTED)

    # ── KPI grid: every KPI, four to a row ──────────────────────────────────
    kpis = spec.get("kpis") or []
    if kpis:
        n_cols = min(4, len(kpis))
        n_rows = -(-len(kpis) // n_cols)
        kpi_table = doc.add_table(rows=n_rows, cols=n_cols)
        for i, kpi in enumerate(kpis):
            cell = kpi_table.rows[i // n_cols].cells[i % n_cols]
            _shade(cell, theme.bare(theme.KPI_TINT))
            label = cell.paragraphs[0]
            _style_run(label.add_run(str(kpi.get("label") or "").upper()), size=7.5, color=theme.MUTED)
            value = cell.add_paragraph()
            _style_run(value.add_run(str(kpi.get("formatted") or kpi.get("value") or "")), size=13, bold=True)
        for j in range(len(kpis), n_rows * n_cols):  # empty trailing cells stay white
            kpi_table.rows[j // n_cols].cells[j % n_cols].text = ""
        doc.add_paragraph()

    # ── executive summary ───────────────────────────────────────────────────
    parts = spec.get("narrative_parts")
    narrative = spec.get("narrative")
    if parts or narrative:
        doc.add_heading("Executive summary", level=2)
        if parts:
            if parts.get("headline"):
                _style_run(doc.add_paragraph().add_run(parts["headline"]), size=12, bold=True)
            if parts.get("summary"):
                doc.add_paragraph(" ".join(parts["summary"]))
            for label, key in (("KEY DRIVERS", "drivers"), ("RISKS AND WATCH-POINTS", "risks"),
                               ("RECOMMENDED ACTIONS", "actions")):
                items = parts.get(key) or []
                if items:
                    sub = doc.add_paragraph()
                    _style_run(sub.add_run(label), size=8, bold=True, color=theme.PRIMARY)
                    sub.paragraph_format.keep_with_next = True
                    for item in items:
                        _add_markdown_runs(doc.add_paragraph(style="List Bullet"), item)
        else:
            for kind, text in _parse_narrative_lines(narrative):
                if kind == "heading":
                    _style_run(doc.add_paragraph().add_run(text.upper()), size=8, bold=True, color=theme.PRIMARY)
                elif kind == "bullet":
                    _add_markdown_runs(doc.add_paragraph(style="List Bullet"), text)
                else:
                    _add_markdown_runs(doc.add_paragraph(), text)

    # ── charts ───────────────────────────────────────────────────────────────
    for chart in spec.get("charts") or []:
        png = _chart_png(chart)
        if not png:
            continue
        doc.add_picture(io.BytesIO(png), width=Cm(17.0))
        if chart.get("takeaway"):
            _style_run(doc.add_paragraph().add_run(chart["takeaway"]), size=9.5, italic=True, color=theme.MUTED)

    # ── tables ───────────────────────────────────────────────────────────────
    for data_table in _all_tables(spec, CANVAS_MAX_ROWS):
        cols = data_table.get("columns") or []
        rows = data_table.get("rows") or []
        if not cols or not rows:
            continue
        doc.add_heading(data_table.get("title") or "Details", level=2)
        tbl = doc.add_table(rows=1 + len(rows), cols=len(cols))
        _borders(tbl, theme.bare(theme.GRID_LINE))
        right = [c.get("align") == "right" for c in cols]
        for i, col in enumerate(cols):
            cell = tbl.rows[0].cells[i]
            _shade(cell, theme.bare(theme.PRIMARY))
            para = cell.paragraphs[0]
            para.alignment = WD_ALIGN_PARAGRAPH.RIGHT if right[i] else WD_ALIGN_PARAGRAPH.LEFT
            _style_run(para.add_run(str(col.get("label") or col.get("key"))), size=8.5, bold=True, color=theme.WHITE)
        _repeat_header(tbl.rows[0])
        for r_i, row in enumerate(rows, start=1):
            first = str(row.get(cols[0].get("key"), "") or "")
            is_total = bool(_TOTAL_ROW.match(first))
            if r_i % 2 == 0:
                for cell in tbl.rows[r_i].cells:
                    _shade(cell, theme.bare(theme.ZEBRA_TINT))
            for c_i, col in enumerate(cols):
                value = row.get(col.get("key"))
                para = tbl.rows[r_i].cells[c_i].paragraphs[0]
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT if right[c_i] else WD_ALIGN_PARAGRAPH.LEFT
                _style_run(para.add_run("" if value is None else str(value)), size=8.5, bold=is_total)
        total_rows = data_table.get("total_rows") or len(rows)
        if total_rows > len(rows) and not data_table.get("hide_total_note"):
            note = doc.add_paragraph()
            _style_run(note.add_run(f"Showing {len(rows):,} of {total_rows:,} rows. "
                                    "The Excel and CSV files include every row."), size=8, color=theme.MUTED)
        doc.add_paragraph()

    # ── notes, warnings, caveats: shaded boxes by tone ──────────────────────
    for tone, text in _callouts(spec):
        box = doc.add_table(rows=1, cols=1)
        cell = box.rows[0].cells[0]
        _shade(cell, theme.bare(_TONE_FILL.get(tone, theme.KPI_TINT)))
        para = cell.paragraphs[0]
        if _TONE_PREFIX.get(tone):
            _style_run(para.add_run(_TONE_PREFIX[tone]), size=9, bold=True, color=_TONE_EDGE[tone])
        _style_run(para.add_run(text), size=9)
        doc.add_paragraph()

    methodology = spec.get("methodology") or []
    if methodology:
        doc.add_heading("About this report", level=2)
        for line in methodology:
            _style_run(doc.add_paragraph().add_run(line), size=8.5, color=theme.MUTED)

    header = section.header.paragraphs[0] if section.header.paragraphs else section.header.add_paragraph()
    _style_run(header.add_run(f"{theme.BRAND_NAME} · {title}"), size=8, color=theme.MUTED)
    footer = section.footer
    left = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    left.text = f"{theme.BRAND_NAME} · Confidential"
    for r in left.runs:
        _style_run(r, size=8, color=theme.MUTED)
    right_p = footer.add_paragraph()
    right_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    right_p.add_run("Page ")
    _field(right_p, "PAGE")
    right_p.add_run(" of ")
    _field(right_p, "NUMPAGES")
    for r in right_p.runs:
        _style_run(r, size=8, color=theme.MUTED)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


#: Rows per table slide; a longer table continues on further slides.
_PPTX_TABLE_ROWS = 12
_PPTX_TABLE_SLIDES = 4
_PPTX_TABLE_COLS = 7


def render_pptx(spec: Dict[str, Any]) -> bytes:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    title, subtitle, meta = _header_bits(spec)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)  # 16:9
    slide_w = 13.333
    content_w = slide_w - 1.2
    rgb = lambda hex_color: RGBColor.from_string(theme.bare(hex_color))  # noqa: E731
    page = {"n": 0}

    def _text(slide, left, top, width, height, text, *, size=14, bold=False, color=theme.TEXT_DARK,
              align=None, italic=False):
        box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
        tf = box.text_frame
        tf.word_wrap = True
        para = tf.paragraphs[0]
        run = para.add_run()
        run.text = text
        run.font.size, run.font.bold, run.font.italic = Pt(size), bold, italic
        run.font.color.rgb = rgb(color)
        if align is not None:
            para.alignment = align
        return tf

    def _slide(heading: str, *, subtitle_text: Optional[str] = None):
        """Title-only layout with the brand's title style: left-aligned, dark, bold.

        The title placeholder is kept (not a plain text box) so the slide keeps
        its title in PowerPoint's outline and for screen readers.
        """
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        page["n"] += 1
        accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(0.42), Inches(0.08), Inches(0.55))
        accent.fill.solid()
        accent.fill.fore_color.rgb = rgb(theme.PRIMARY)
        accent.line.fill.background()
        t = slide.shapes.title
        t.left, t.top, t.width, t.height = Inches(0.8), Inches(0.3), Inches(content_w - 0.2), Inches(0.8)
        t.text = heading
        para = t.text_frame.paragraphs[0]
        para.alignment = PP_ALIGN.LEFT
        for run in para.runs:
            run.font.size, run.font.bold = Pt(26), True
            run.font.color.rgb = rgb(theme.TEXT_DARK)
        top = 1.25
        if subtitle_text:
            _text(slide, 0.8, 1.1, content_w - 0.2, 0.7, subtitle_text, size=15, color=theme.MUTED)
            top = 1.85
        footer = f"{theme.BRAND_NAME} · {title} · Confidential"
        _text(slide, 0.6, 7.0, content_w - 1.0, 0.35, footer[:140], size=9, color=theme.MUTED)
        _text(slide, slide_w - 1.6, 7.0, 1.0, 0.35, str(page["n"]), size=9, color=theme.MUTED, align=PP_ALIGN.RIGHT)
        return slide, top

    # ── title slide ─────────────────────────────────────────────────────────
    cover = prs.slides.add_slide(prs.slide_layouts[5])
    page["n"] += 1
    band = cover.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.35), prs.slide_height)
    band.fill.solid()
    band.fill.fore_color.rgb = rgb(theme.PRIMARY)
    band.line.fill.background()
    _text(cover, 1.0, 2.2, 10, 0.5, theme.BRAND_NAME, size=14, bold=True, color=theme.PRIMARY)
    t = cover.shapes.title
    t.left, t.top, t.width, t.height = Inches(1.0), Inches(2.7), Inches(11), Inches(1.4)
    t.text = title
    for run in t.text_frame.paragraphs[0].runs:
        run.font.size, run.font.bold = Pt(40), True
        run.font.color.rgb = rgb(theme.TEXT_DARK)
    t.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    _text(cover, 1.0, 4.2, 11, 0.9, "\n".join(p for p in (subtitle, meta) if p), size=14, color=theme.MUTED)

    # ── key figures: cards, eight per slide ────────────────────────────────
    kpis = spec.get("kpis") or []
    for start in range(0, len(kpis), 8):
        slide, top = _slide("Key figures")
        chunk = kpis[start:start + 8]
        per_row = min(4, len(chunk))
        card_w = (content_w - 0.3 * (per_row - 1)) / per_row
        for i, kpi in enumerate(chunk):
            left = 0.6 + (i % 4) * (card_w + 0.3)
            y = top + 0.3 + (i // 4) * 1.9
            card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left), Inches(y), Inches(card_w), Inches(1.6))
            card.fill.solid()
            card.fill.fore_color.rgb = rgb(theme.KPI_TINT)
            card.line.fill.background()
            card.adjustments[0] = 0.08
            _text(slide, left + 0.2, y + 0.2, card_w - 0.4, 0.4, str(kpi.get("label") or "").upper(), size=11, color=theme.MUTED)
            value = str(kpi.get("formatted") or kpi.get("value") or "")
            _text(slide, left + 0.2, y + 0.65, card_w - 0.4, 0.8, value, size=24 if len(value) <= 14 else 18, bold=True)

    # ── executive summary: the finding on the left, the detail on the right ─
    parts = spec.get("narrative_parts")
    narrative = spec.get("narrative")
    if parts:
        slide, top = _slide("Executive summary")
        left_tf = _text(slide, 0.6, top + 0.2, 5.6, 1.2, parts.get("headline") or "", size=22, bold=True)
        if parts.get("summary"):
            para = left_tf.add_paragraph()
            para.space_before = Pt(14)
            run = para.add_run()
            run.text = " ".join(parts["summary"])
            run.font.size = Pt(14)
            run.font.color.rgb = rgb(theme.MUTED)
        right_box = slide.shapes.add_textbox(Inches(6.7), Inches(top + 0.2), Inches(content_w - 6.1), Inches(5.3))
        tf = right_box.text_frame
        tf.word_wrap = True
        first = True
        for label, key in (("KEY DRIVERS", "drivers"), ("RISKS AND WATCH-POINTS", "risks"), ("RECOMMENDED ACTIONS", "actions")):
            items = parts.get(key) or []
            if not items:
                continue
            para = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            para.space_before = Pt(10)
            run = para.add_run()
            run.text = label
            run.font.size, run.font.bold = Pt(11), True
            run.font.color.rgb = rgb(theme.PRIMARY)
            for item in items:
                bullet = tf.add_paragraph()
                bullet.space_before = Pt(4)
                r = bullet.add_run()
                r.text = f"•  {item}"
                r.font.size = Pt(13)
                r.font.color.rgb = rgb(theme.TEXT_DARK)
    elif narrative:
        slide, top = _slide("Executive summary")
        box = slide.shapes.add_textbox(Inches(0.6), Inches(top + 0.2), Inches(content_w), Inches(5.2))
        tf = box.text_frame
        tf.word_wrap = True
        for i, (kind, text) in enumerate(_parse_narrative_lines(narrative)):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            run = para.add_run()
            run.text = ("•  " if kind == "bullet" else "") + re.sub(r"\*\*(.+?)\*\*", r"\1", text)
            run.font.size = Pt(14 if kind != "heading" else 12)
            run.font.bold = kind == "heading"

    # ── charts: the takeaway is the slide's subtitle ────────────────────────
    for chart in spec.get("charts") or []:
        cats = chart.get("categories") or []
        series = chart.get("series") or []
        if not cats or not series:
            continue
        slide, top = _slide(chart.get("title") or "Chart", subtitle_text=chart.get("takeaway"))
        _pptx_chart(slide, chart, Inches(0.6), Inches(top + 0.1), Inches(content_w), Inches(6.85 - top - 0.1),
                    CategoryChartData, XL_CHART_TYPE, RGBColor, Pt)

    # ── tables: twelve rows a slide, continued, never silently cut ──────────
    for table in _all_tables(spec, CANVAS_MAX_ROWS):
        all_cols = table.get("columns") or []
        cols = all_cols[:_PPTX_TABLE_COLS]
        rows = table.get("rows") or []
        if not cols or not rows:
            continue
        right = [c.get("align") == "right" for c in cols]
        chunks = [rows[i:i + _PPTX_TABLE_ROWS] for i in range(0, len(rows), _PPTX_TABLE_ROWS)][:_PPTX_TABLE_SLIDES]
        shown = sum(len(c) for c in chunks)
        total_rows = max(table.get("total_rows") or len(rows), len(rows))
        for n, chunk in enumerate(chunks):
            heading = table.get("title") or "Details"
            slide, top = _slide(heading if n == 0 else f"{heading} (continued)")
            shape = slide.shapes.add_table(1 + len(chunk), len(cols), Inches(0.6), Inches(top + 0.1),
                                           Inches(content_w), Inches(0.38 * (1 + len(chunk))))
            tbl = shape.table
            for i, col in enumerate(cols):
                c = tbl.cell(0, i)
                c.text = str(col.get("label") or col.get("key"))
                c.fill.solid()
                c.fill.fore_color.rgb = rgb(theme.PRIMARY)
                para = c.text_frame.paragraphs[0]
                para.alignment = PP_ALIGN.RIGHT if right[i] else PP_ALIGN.LEFT
                for run in para.runs:
                    run.font.size, run.font.bold = Pt(12), True
                    run.font.color.rgb = rgb(theme.WHITE)
            for r_i, row in enumerate(chunk, start=1):
                first = str(row.get(cols[0].get("key"), "") or "")
                is_total = bool(_TOTAL_ROW.match(first))
                for c_i, col in enumerate(cols):
                    c = tbl.cell(r_i, c_i)
                    value = row.get(col.get("key"))
                    c.text = "" if value is None else str(value)
                    c.fill.solid()
                    c.fill.fore_color.rgb = rgb(theme.ZEBRA_TINT if r_i % 2 == 0 else theme.WHITE)
                    para = c.text_frame.paragraphs[0]
                    para.alignment = PP_ALIGN.RIGHT if right[c_i] else PP_ALIGN.LEFT
                    for run in para.runs:
                        run.font.size, run.font.bold = Pt(11), is_total
                        run.font.color.rgb = rgb(theme.TEXT_DARK)
            notes = []
            if n == len(chunks) - 1 and total_rows > shown:
                notes.append(f"Showing {shown:,} of {total_rows:,} rows. The Excel file includes every row.")
            if len(all_cols) > len(cols) and n == 0:
                notes.append(f"Showing {len(cols)} of {len(all_cols)} columns. The Excel file includes all of them.")
            if notes:
                _text(slide, 0.6, 6.55, content_w, 0.4, " ".join(notes), size=10, color=theme.MUTED)

    # ── notes, warnings and sources ─────────────────────────────────────────
    about = [*(_callout_text(t, x) for t, x in _callouts(spec)), *(spec.get("methodology") or [])]
    if about:
        slide, top = _slide("About this report")
        box = slide.shapes.add_textbox(Inches(0.6), Inches(top + 0.1), Inches(content_w), Inches(5.4))
        tf = box.text_frame
        tf.word_wrap = True
        for i, line in enumerate(about):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            run = para.add_run()
            run.text = line
            run.font.size = Pt(13)
            run.font.color.rgb = rgb(_TONE_EDGE["warning"] if line.startswith("Warning:") else theme.TEXT_DARK)
            para.space_after = Pt(6)

    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def _pptx_chart(slide: Any, chart: Dict[str, Any], left: Any, top: Any, width: Any, height: Any,
                CategoryChartData: Any, XL_CHART_TYPE: Any, RGBColor: Any, Pt: Any) -> None:
    """A native PowerPoint chart with the report's resolved colors and labels."""
    cats = chart.get("categories") or []
    series = chart.get("series") or []
    ctype = (chart.get("chart_type") or "bar").lower()
    data = CategoryChartData()
    data.categories = cats
    waterfall_values: List[float] = []
    if ctype == "waterfall":
        waterfall_values = [float(v) if v is not None else 0.0 for v in (series[0].get("data") or [])][: len(cats)]
        bases, heights = _waterfall_parts(waterfall_values, set(chart.get("total_indices") or []))
        data.add_series("Base", tuple(bases))
        data.add_series(series[0].get("name") or "Amount", tuple(heights))
    else:
        for s in series:
            values = list(s.get("data") or [])[: len(cats)]
            data.add_series(s.get("name") or "Value", tuple(values + [None] * (len(cats) - len(values))))
    xl = {
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "area": XL_CHART_TYPE.AREA,
        "pie": XL_CHART_TYPE.PIE,
        "donut": XL_CHART_TYPE.DOUGHNUT,
        "hbar": XL_CHART_TYPE.BAR_CLUSTERED,
        "stacked_bar": XL_CHART_TYPE.COLUMN_STACKED,
        "waterfall": XL_CHART_TYPE.COLUMN_STACKED,
    }.get(ctype, XL_CHART_TYPE.COLUMN_CLUSTERED)
    graphic = slide.shapes.add_chart(xl, left, top, width, height, data).chart
    graphic.font.size = Pt(12)
    if ctype == "hbar":
        graphic.category_axis.reverse_order = True  # largest on top, as in every other output
    # Same resolved colors as the canvas and the PDF, not Office's theme.
    series_colors, point_colors = _chart_colors(chart)
    if ctype == "waterfall":
        base_series, bar_series = graphic.plots[0].series
        base_series.format.fill.background()
        base_series.format.line.fill.background()
        totals = set(chart.get("total_indices") or [])
        for p_i, p_color in enumerate((point_colors or [])[: len(cats)]):
            point = bar_series.points[p_i]
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = RGBColor.from_string(theme.bare(p_color))
            label = point.data_label.text_frame
            label.text = _value_label(waterfall_values[p_i], signed=p_i not in totals)
            label.paragraphs[0].runs[0].font.size = Pt(11)
        graphic.plots[0].gap_width = 60
        graphic.has_legend = False
        return
    if ctype in ("bar", "hbar") and len(series) == 1 and len(cats) <= 15:
        for p_i, v in enumerate((series[0].get("data") or [])[: len(cats)]):
            if v is None:
                continue
            label = graphic.plots[0].series[0].points[p_i].data_label.text_frame
            label.text = _value_label(float(v))
            label.paragraphs[0].runs[0].font.size = Pt(11)
    graphic.has_legend = len(series) > 1 or ctype in ("pie", "donut")
    if graphic.has_legend:
        graphic.legend.include_in_layout = False
        graphic.legend.font.size = Pt(12)
    for s_i, plot_series in enumerate(graphic.plots[0].series):
        color = RGBColor.from_string(theme.bare(
            series_colors[s_i] if s_i < len(series_colors) else theme.OTHER_COLOR
        ))
        if ctype == "line":
            plot_series.format.line.color.rgb = color
            plot_series.smooth = False
        else:
            plot_series.format.fill.solid()
            plot_series.format.fill.fore_color.rgb = color
        if point_colors and s_i == 0:
            for p_i, p_color in enumerate(point_colors[: len(cats)]):
                point = plot_series.points[p_i]
                point.format.fill.solid()
                point.format.fill.fore_color.rgb = RGBColor.from_string(theme.bare(p_color))
    if ctype in ("pie", "donut"):
        plot = graphic.plots[0]
        plot.has_data_labels = True
        plot.data_labels.show_percentage = True
        plot.data_labels.show_value = False
        plot.data_labels.number_format = "0%"
        plot.data_labels.number_format_is_linked = False
        plot.data_labels.font.size = Pt(11)


def render_md(spec: Dict[str, Any]) -> bytes:
    """Self-contained Markdown: charts embed as base64 PNG data URIs, no side assets."""
    import base64

    title, _, meta = _header_bits(spec)
    lines: List[str] = [f"**{theme.BRAND_NAME}**", "", f"# {title}", "", f"*{meta}*", "", "---", ""]

    kpis = spec.get("kpis") or []
    if kpis:
        lines += ["| Metric | Value |", "| --- | --- |"]
        for k in kpis:
            label = str(k.get("label") or "").replace("|", "/")
            value = str(k.get("formatted") if k.get("formatted") is not None else (k.get("value") or "")).replace("|", "/")
            lines.append(f"| {label} | {value} |")
        lines.append("")

    narrative = spec.get("narrative")
    if narrative:
        lines.append("## Executive summary")
        lines.append("")
        lines.append(narrative.strip())
        lines.append("")

    for chart in spec.get("charts") or []:
        png = _chart_png(chart)
        if not png:
            continue
        chart_title = chart.get("title") or "Chart"
        lines.append(f"## {chart_title}")
        if chart.get("takeaway"):
            lines += ["", f"*{chart['takeaway']}*"]
        b64 = base64.b64encode(png).decode("ascii")
        lines.append(f"![{chart_title}](data:image/png;base64,{b64})")
        lines.append("")
        # Many Markdown viewers (GitHub, Slack, some wikis) strip data-URI
        # images; the chart's own figures as a table survive everywhere.
        cats = chart.get("categories") or []
        series = chart.get("series") or []
        if cats and series:
            lines.append("| " + " | ".join([str(chart.get("x_label") or "Category")] + [str(s.get("name")) for s in series]) + " |")
            lines.append("| --- | " + " | ".join("---:" for _ in series) + " |")
            for i, cat in enumerate(cats):
                cells = []
                for s in series:
                    data = s.get("data") or []
                    v = data[i] if i < len(data) else None
                    cells.append("—" if v is None else f"{v:,.2f}")
                lines.append("| " + " | ".join([str(cat).replace("|", "/")] + cells) + " |")
            lines.append("")

    for table in _all_tables(spec, CANVAS_MAX_ROWS):
        columns = table.get("columns") or []
        rows = table.get("rows") or []
        if not columns or not rows:
            continue
        if table.get("title"):
            lines.append(f"## {table['title']}")
        keys = [c.get("key") for c in columns]
        headers = [str(c.get("label") or k) for c, k in zip(columns, keys)]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            cells = [str(row.get(k, "") or "").replace("|", "/").replace("\n", " ") for k in keys]
            lines.append("| " + " | ".join(cells) + " |")
        if table.get("truncated"):
            lines.append("")
            lines.append(f"*Showing {len(rows)} of {table.get('total_rows')} rows.*")
        lines.append("")

    callouts = _callouts(spec)
    for tone, text in callouts:
        prefix = _TONE_PREFIX.get(tone, "")
        lines.append(f"> **{prefix.strip()}** {text}" if prefix else f"> {text}")
        lines.append("")

    methodology = spec.get("methodology") or []
    if methodology:
        lines += ["## About this report", ""]
        lines += [f"- {line}" for line in methodology]
        lines.append("")

    return (("\n".join(lines)).strip() + "\n").encode("utf-8")


RENDERERS = {
    "csv": render_csv,
    "xlsx": render_xlsx,
    "pdf": render_pdf,
    "docx": render_docx,
    "pptx": render_pptx,
    "md": render_md,
}


def generate_and_store(
    spec: Dict[str, Any],
    fmt: str,
    *,
    user_id: int,
    organization_id: Optional[int] = None,
    session_id: Optional[str] = None,
    regenerated_from: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Render one format, store it (with its spec, for later regeneration),
    and return the artifact block. Records per-format render metrics."""
    from gemini_brain.artifacts.store import ArtifactTooLarge
    from gemini_brain.observability.metrics import METRICS

    fmt = (fmt or "").lower()
    renderer = RENDERERS.get(fmt)
    if renderer is None:
        return None
    t0 = time.perf_counter()
    try:
        content = renderer(spec)
    except Exception:
        METRICS.report_renders.record(fmt, "failed", ms=(time.perf_counter() - t0) * 1000)
        raise
    ms = (time.perf_counter() - t0) * 1000
    if not content:
        METRICS.report_renders.record(fmt, "failed", ms=ms)
        return None
    try:
        rec: ArtifactRecord = put(
            content,
            filename=_slug(spec.get("title") or "report", fmt),
            mime=MIME[fmt],
            user_id=user_id,
            organization_id=organization_id,
            session_id=session_id,
            ttl=TTL_SECONDS,
            fmt=fmt,
            spec=spec,
            regenerated_from=regenerated_from,
        )
    except ArtifactTooLarge as e:
        METRICS.report_renders.record(fmt, "too_large", ms=ms, size=len(content))
        logger.warning("Artifact refused: %s", e)
        return None
    METRICS.report_renders.record(fmt, "ok", ms=ms, size=len(content))
    logger.info("Rendered %s in %.0f ms (%s bytes)", fmt, ms, f"{len(content):,}")
    return {
        "type": "artifact",
        "id": rec.id,
        "kind": fmt,
        "filename": rec.filename,
        "mime": rec.mime,
        "expires_in": TTL_SECONDS,
        "expires_at": datetime.fromtimestamp(rec.expires_at, tz=timezone.utc).isoformat(),
    }


_RENDER_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="report-render")


def render_formats(spec: Dict[str, Any], formats: List[str], **store_kwargs: Any) -> Dict[str, Optional[Dict[str, Any]]]:
    """Render several formats in parallel, each bounded by the render timeout.

    Returns {fmt: artifact block or None}. A format that fails or runs past
    REPORT_RENDER_TIMEOUT_SECONDS is None; the others still ship. (A timed-out
    render keeps its worker thread until it finishes, but nothing waits on it.)
    """
    from gemini_brain.config.settings import settings
    from gemini_brain.observability.metrics import METRICS

    ctx = contextvars.copy_context()
    futures = {
        fmt: _RENDER_POOL.submit(ctx.copy().run, generate_and_store, spec, fmt, **store_kwargs)
        for fmt in formats
    }
    deadline = time.monotonic() + settings.report_render_timeout_seconds
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for fmt, future in futures.items():
        try:
            out[fmt] = future.result(timeout=max(0.0, deadline - time.monotonic()))
        except concurrent.futures.TimeoutError:
            METRICS.report_renders.record(fmt, "timeout")
            logger.warning("Rendering %s exceeded %.0fs; the report goes out without it",
                           fmt, settings.report_render_timeout_seconds)
            out[fmt] = None
        except Exception as e:
            logger.warning("Artifact generation skipped (%s): %s", fmt, e)
            out[fmt] = None
    return out
