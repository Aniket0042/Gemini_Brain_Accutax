from dataclasses import dataclass, field

from gemini_brain.insights.schemas.response import Bullet, Chart


@dataclass
class WidgetAnalysis:
    """
    Output of a widget's deterministic (non-LLM) analysis step.

    facts_text is the only thing handed to the LLM -- it must contain every
    number the LLM is allowed to reference. metric_bullets and chart are
    built straight from the caller's own data and returned as-is, with no
    LLM involvement, so displayed figures always match what the frontend
    already computed and sent us.
    """

    facts_text: str
    metric_bullets: list[Bullet] = field(default_factory=list)
    chart: Chart | None = None
