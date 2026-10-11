"""
models.py — The Claude models a user can pick to answer (plan docs/CONTEXT_MODELS_REPORTS_PLAN.md, phase 2).

The same agent answers with every model: same tools, data, rules and thread history. The picked model
only plans the tool calls and writes the answer. UAE VAT law answers keep their own model (vat_kb).

The browser sends a key ("sonnet-5"), never a Bedrock model id. Which keys are offered is a deployment
setting (AGENT_MODELS), so a model is switched on only after it passes the real and golden question sets.
Models other than the default can be limited to a list of users (AGENT_MODEL_USERS) until usage limits
exist. An unknown, switched-off or not-allowed key answers with the default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.config.constants import HAIKU3_ID, HAIKU45_ID, SONNET5_ID
from gemini_brain.config.pricing import bedrock_price_for_model
from gemini_brain.config.settings import settings

AUTO = "auto"


@dataclass(frozen=True)
class AgentModel:
    key: str
    label: str
    tier: str
    description: str
    model_id: str
    #: Bedrock prompt caching (cache points on the system prompt and tools). Claude 3 Haiku refuses it.
    cache: bool = True

    def bedrock_id(self) -> str:
        """The default model follows AGENT_MODEL_ID when it is set, as before the picker."""
        if self.key == DEFAULT_KEY and settings.agent_model_id:
            return settings.agent_model_id
        return self.model_id

    def to_public(self) -> Dict[str, Any]:
        """The model in the shape of api.models.ModelInfo."""
        rates = bedrock_price_for_model(self.model_id)
        return {
            "key": self.key,
            "label": self.label,
            "description": f"{self.tier}. {self.description}",
            "provider": "bedrock",
            "supports": ["tools"],
            "efforts": [],
            "cost_per_mtok": {"input": rates["input"], "output": rates["output"]},
            "latency_class": "standard" if self.key == DEFAULT_KEY else "fast",
            "primary": True,
            "available": True,
        }


MODELS: Sequence[AgentModel] = (
    AgentModel("sonnet-5", "Claude Sonnet 5", "Balanced",
               "Most accurate; the default for analysis, comparisons and reports.", SONNET5_ID),
    AgentModel("haiku-4.5", "Claude Haiku 4.5", "Fast",
               "Quicker and about half the cost; best for lookups and lists.", HAIKU45_ID),
    AgentModel("haiku-3", "Claude Haiku 3", "Cheapest",
               "Lowest cost; simple single-figure lookups.", HAIKU3_ID, cache=False),
)
DEFAULT_KEY = "sonnet-5"
_BY_KEY = {m.key: m for m in MODELS}


def default() -> AgentModel:
    return _BY_KEY[DEFAULT_KEY]


def get(key: Optional[str]) -> Optional[AgentModel]:
    return _BY_KEY.get(str(key or "").strip().lower())


def enabled() -> List[AgentModel]:
    """The models this deployment offers (AGENT_MODELS), default first."""
    keys = [k.strip().lower() for k in (settings.agent_models or "").split(",") if k.strip()]
    out = [default()] + [m for m in MODELS if m.key in keys and m.key != DEFAULT_KEY]
    return out


def allowed(user_id: Any = None, email: Optional[str] = None) -> List[AgentModel]:
    """The models this user may pick: the default always; the others when AGENT_MODEL_USERS is empty or
    lists the user's email (any case) or id."""
    people = {p.strip().lower() for p in (settings.agent_model_users or "").split(",") if p.strip()}
    if people and str(user_id).lower() not in people and (email or "").strip().lower() not in people:
        return [default()]
    return enabled()


def resolve(requested: Optional[str], allowed_keys: Sequence[str], saved: Optional[str] = None) -> AgentModel:
    """The model that answers: the requested key if allowed; for "auto" or none, the thread's saved key if
    still allowed; otherwise the default."""
    allowed_set = set(allowed_keys or [DEFAULT_KEY])
    key = str(requested or "").strip().lower()
    if key in ("", AUTO):
        key = str(saved or "").strip().lower()
    model = get(key)
    return model if model and model.key in allowed_set else default()


def caches(model_id: str) -> bool:
    """Whether the agent marks prompts for caching with this Bedrock model."""
    for m in MODELS:
        if model_id in (m.model_id, m.bedrock_id()):
            return m.cache
    return "claude-3-haiku" not in (model_id or "").lower()
