"""registry.py — declarative catalog of the models a user may choose.

The API serves this so the frontend picker never hardcodes model ids, and so a
model can be deprecated or restricted per-organization without a code change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gemini_brain.config.constants import HAIKU3_ID, HAIKU45_ID, SONNET35_ID
from gemini_brain.config.pricing import GEMINI_INPUT_PRICE, GEMINI_OUTPUT_PRICE
from gemini_brain.config.settings import settings


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    description: str
    model_id: str
    provider: str = "bedrock"
    context_window: int = 200_000
    max_output: int = 8_192
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_thinking: bool = False
    cost_in_per_mtok: float = 0.0
    cost_out_per_mtok: float = 0.0
    latency_class: str = "fast"
    #: Effort tiers this model may be used for. Auto never exceeds these.
    efforts: List[str] = field(default_factory=lambda: ["quick", "standard"])
    deprecated: bool = False
    #: Shown in the picker's primary list; the rest sit under "More models".
    primary: bool = False
    #: Set when the model needs something the deployment may not have.
    requires: str = ""

    def to_public(self, available: bool = True) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "provider": self.provider,
            "context_window": self.context_window,
            "max_output": self.max_output,
            "supports": [
                name
                for name, on in (
                    ("tools", self.supports_tools),
                    ("streaming", self.supports_streaming),
                    ("thinking", self.supports_thinking),
                )
                if on
            ],
            "efforts": list(self.efforts),
            "cost_per_mtok": {"in": self.cost_in_per_mtok, "out": self.cost_out_per_mtok},
            "latency_class": self.latency_class,
            "deprecated": self.deprecated,
            "primary": self.primary,
            "requires": self.requires,
            "available": available,
        }


MODEL_REGISTRY: Dict[str, ModelSpec] = {
    "haiku-4.5": ModelSpec(
        key="haiku-4.5",
        label="Claude Haiku 4.5",
        primary=True,
        description="Best for lookups, lists and straightforward reports.",
        model_id=HAIKU45_ID,
        cost_in_per_mtok=0.80,
        cost_out_per_mtok=4.00,
        latency_class="fast",
        efforts=["quick", "standard", "thorough"],
    ),
    "sonnet-3.5": ModelSpec(
        key="sonnet-3.5",
        label="Claude Sonnet 3.5",
        primary=True,
        description="Multi-period analysis, forecasting and strategic summaries.",
        model_id=SONNET35_ID,
        max_output=8_192,
        supports_thinking=True,
        cost_in_per_mtok=3.00,
        cost_out_per_mtok=15.00,
        latency_class="slow",
        efforts=["standard", "thorough", "exhaustive"],
    ),
    "haiku-3": ModelSpec(
        key="haiku-3",
        label="Claude Haiku 3",
        description="Lowest cost. Simple single-figure lookups only.",
        model_id=HAIKU3_ID,
        context_window=200_000,
        max_output=4_096,
        cost_in_per_mtok=0.25,
        cost_out_per_mtok=1.25,
        latency_class="fast",
        efforts=["quick"],
    ),
    "gemini-2.5-flash": ModelSpec(
        key="gemini-2.5-flash",
        label="Gemini 2.5 Flash",
        description="Google's fast model. Strong on summarising and explanation.",
        model_id="gemini-2.5-flash",
        provider="gemini",
        context_window=1_000_000,
        max_output=8_192,
        supports_tools=False,
        cost_in_per_mtok=GEMINI_INPUT_PRICE,
        cost_out_per_mtok=GEMINI_OUTPUT_PRICE,
        latency_class="fast",
        efforts=["quick", "standard", "thorough"],
        primary=True,
        requires="GEMINI_API_KEY",
    ),
    "gemini-2.5-pro": ModelSpec(
        key="gemini-2.5-pro",
        label="Gemini 2.5 Pro",
        description="Google's largest model. Long context, deeper analysis.",
        model_id="gemini-2.5-pro",
        provider="gemini",
        context_window=1_000_000,
        max_output=8_192,
        supports_tools=False,
        cost_in_per_mtok=1.25,
        cost_out_per_mtok=10.00,
        latency_class="slow",
        efforts=["standard", "thorough", "exhaustive"],
        requires="GEMINI_API_KEY",
    ),
}

#: Key callers may pass to mean "let the system decide".
AUTO_KEY = "auto"

_DEFAULT_KEY = "haiku-4.5"


def list_models(
    allowed_keys: Optional[List[str]] = None,
    health: Optional[Dict[str, bool]] = None,
) -> List[Dict[str, Any]]:
    """Public model list, optionally filtered by org allowlist and health."""
    health = health or {}
    out: List[Dict[str, Any]] = []
    for spec in MODEL_REGISTRY.values():
        if allowed_keys is not None and spec.key not in allowed_keys:
            continue
        out.append(spec.to_public(available=health.get(spec.key, is_configured(spec))))
    return out


def is_configured(spec: ModelSpec) -> bool:
    """Whether this deployment has the credentials the model needs."""
    if spec.provider == "gemini":
        return bool(settings.gemini_api_key)
    return True


def build_adapter(spec: ModelSpec) -> Any:
    """Construct the adapter that can actually answer with this model."""
    if spec.provider == "gemini":
        from gemini_brain.reasoning.gemini_adapter import GeminiAdapter

        return GeminiAdapter(model_id=spec.model_id, label=spec.label)

    from gemini_brain.reasoning.bedrock_client import BedrockAdapter

    return BedrockAdapter(model_id=spec.model_id, label=spec.label)


def resolve_model(key: Optional[str]) -> ModelSpec:
    """Resolve a model key to its spec, falling back to the default."""
    if not key or key in (AUTO_KEY, "gemini_brain"):
        return MODEL_REGISTRY[_DEFAULT_KEY]
    return MODEL_REGISTRY.get(key, MODEL_REGISTRY[_DEFAULT_KEY])


def model_for_id(model_id: str) -> Optional[ModelSpec]:
    for spec in MODEL_REGISTRY.values():
        if spec.model_id == model_id:
            return spec
    return None
