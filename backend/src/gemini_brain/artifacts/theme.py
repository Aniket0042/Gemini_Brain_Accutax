"""Shared brand constants for every artifact renderer (PDF/XLSX/DOCX/PPTX/MD).

One source of truth so the wordmark color, table accents, and chart palette stay
identical across formats and match the live canvas. Chart colors are resolved
here once per chart (`resolve_chart_colors`) and shipped inside the spec, so the
canvas and every file renderer paint the same entity the same color instead of
each renderer applying its own rules.

The frontend mirror (frontend/src/components/canvas/chartTheme.js) is only a
fallback for legacy chart blocks that arrive without resolved colors; a unit
test keeps its hex lists equal to these.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "BRAND_NAME", "PRIMARY", "TEXT_DARK", "MUTED", "GRID_LINE", "KPI_TINT",
    "ZEBRA_TINT", "WHITE", "CATEGORICAL", "OTHER_COLOR", "SEMANTIC_SLOTS",
    "series_color", "resolve_chart_colors", "is_opposing", "bare",
]

BRAND_NAME = "ACCUTAX"

PRIMARY = "#0E8A75"
TEXT_DARK = "#14201C"
MUTED = "#526059"
GRID_LINE = "#D0D5DD"
KPI_TINT = "#E1F1EC"
ZEBRA_TINT = "#F4F7F6"
WHITE = "#FFFFFF"

#: Categorical hues in fixed order, never cycled. Validated with the dataviz
#: palette checker on white: adjacent CVD ΔE >= 15, normal-vision floor pass.
#: Slots 5-6 sit below 3:1 contrast, so pie/donut slices always carry direct
#: labels. A category past slot 7 folds into "Other" (OTHER_COLOR), never a
#: generated or repeated hue.
CATEGORICAL = [
    PRIMARY,    # 1 teal (brand)
    "#2A78D6",  # 2 blue
    "#EB6834",  # 3 orange
    "#4A3AA7",  # 4 violet
    "#E87BA4",  # 5 magenta
    "#EDA100",  # 6 yellow
    "#E34948",  # 7 red
]
OTHER_COLOR = "#94A3B8"

#: Series/category names that always get the same slot, whatever their position,
#: so "Revenue" is teal and "Expenses" orange in every chart and every format.
#: Order matters: "net" must not match inside "revenue", so tokens are checked
#: as whole words first (see _semantic_slot).
SEMANTIC_SLOTS = (
    (("revenue", "income", "sales", "receipts", "inflow"), 0),
    (("expense", "expenses", "cost", "costs", "spend", "outflow", "purchases"), 2),
    (("profit", "net", "cashflow", "cash", "margin"), 1),
)


def bare(hex_color: str) -> str:
    """Strip '#' - openpyxl PatternFill/Font and python-docx/pptx RGBColor want 6 bare hex chars."""
    return hex_color.lstrip("#")


def _tokens(name: Any) -> List[str]:
    out: List[str] = []
    word = ""
    for ch in str(name or "").lower():
        if ch.isalnum():
            word += ch
        elif word:
            out.append(word)
            word = ""
    if word:
        out.append(word)
    return out


def _semantic_slot(name: Any) -> Optional[int]:
    toks = _tokens(name)
    joined = "".join(toks)
    for words, slot in SEMANTIC_SLOTS:
        if any(w in toks for w in words) or joined in words:
            return slot
    return None


def is_opposing(names: Sequence[Any]) -> bool:
    """Inflow vs outflow (revenue vs expenses): two sides of a result, not parts
    of one whole — their sum, share of sum, or stacked height means nothing."""
    slots = {_semantic_slot(n) for n in names}
    return 0 in slots and 2 in slots


def series_color(name: Any, index: int) -> str:
    """Color for one series: semantic slot when the name has one, else slot by position."""
    if str(name or "").strip().lower() == "other":
        return OTHER_COLOR
    slot = _semantic_slot(name)
    if slot is not None:
        return CATEGORICAL[slot]
    return CATEGORICAL[index] if index < len(CATEGORICAL) else OTHER_COLOR


def _category_colors(categories: Sequence[Any]) -> List[str]:
    """One color per category (pie/donut slices, or semantic single-series bars).

    Semantic names keep their slot; the rest take the remaining slots in order.
    Anything past the palette is OTHER_COLOR — build_report_spec folds pie tails
    into "Other" before this runs, so that only happens for legacy blocks.
    """
    semantic = [_semantic_slot(c) for c in categories]
    taken = {s for s in semantic if s is not None}
    free = [i for i in range(len(CATEGORICAL)) if i not in taken]
    out: List[str] = []
    for cat, slot in zip(categories, semantic):
        if str(cat or "").strip().lower() == "other":
            out.append(OTHER_COLOR)
        elif slot is not None:
            out.append(CATEGORICAL[slot])
        elif free:
            out.append(CATEGORICAL[free.pop(0)])
        else:
            out.append(OTHER_COLOR)
    return out


def resolve_chart_colors(chart: Dict[str, Any]) -> Dict[str, Any]:
    """Write `series_colors` (always) and `point_colors` (when color encodes a
    category) onto `chart`, in place. Returns the chart.

    Rules — color only ever encodes identity:
      * pie/donut: one color per slice.
      * single-series bar whose categories are all semantic names (e.g.
        Revenue vs Expenses): one color per bar, by meaning.
      * any other single series: one brand color for every mark. Coloring each
        bar differently would encode nothing.
      * multi-series: one color per series, semantic first.
    """
    ctype = str(chart.get("chart_type") or "bar").lower()
    series = chart.get("series") or []
    cats = chart.get("categories") or []
    chart.pop("bar_colors", None)  # legacy per-bar rainbow, superseded
    if len(series) == 1:
        chart["series_colors"] = [PRIMARY if _semantic_slot(series[0].get("name")) is None
                                  else series_color(series[0].get("name"), 0)]
    else:
        chart["series_colors"] = [series_color(s.get("name"), i) for i, s in enumerate(series)]
    if ctype in ("pie", "donut"):
        chart["point_colors"] = _category_colors(cats)
    elif ctype == "waterfall":
        # Totals in brand teal; a step down in the expense color, a step up in blue.
        totals = set(chart.get("total_indices") or [])
        values = (series[0].get("data") if series else None) or []
        chart["point_colors"] = [
            PRIMARY if i in totals
            else (CATEGORICAL[2] if (i < len(values) and values[i] is not None and values[i] < 0) else CATEGORICAL[1])
            for i in range(len(cats))
        ]
    elif (
        ctype in ("bar", "hbar")
        and len(series) == 1
        and len(cats) > 1
        and all(_semantic_slot(c) is not None for c in cats)
        and len({_semantic_slot(c) for c in cats}) == len(cats)
    ):
        chart["point_colors"] = _category_colors(cats)
    else:
        chart.pop("point_colors", None)
    return chart
