"""
law.py — UAE VAT law questions go to the existing knowledge-base answer, not the tool loop.

The current answer path writes law answers in one model call from the FTA
sources (vat_kb.augment): same detector, same confident-match search, same
rules and prompt. In the replay it answered all 19 law questions correctly in
about 4 s, while the tool loop took 7–40 s and varied between runs. A question
about the organizations' own figures ("VAT payable per entity", "compare tax
payable") never takes this route.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gemini_brain.agent.law")

#: vat_kb.augment answers law questions for these question types; 6 is the knowledge answer.
KNOWLEDGE_TYPE = 6

# Words that ask for the selected organizations' figures. "Company" and "our" stay out:
# law questions use them too ("A company leaves our VAT tax group").
_ABOUT_THE_ORGS = re.compile(
    r"\b(organi[sz]ations?|orgs?|entit(y|ies)|each|per|compare|comparison|rank\w*|across|highest|lowest)\b"
    r"|\btop\s+\d+\b",
    re.I,
)


@dataclass
class LawAnswer:
    answer: str
    usage: Dict[str, Any] = field(default_factory=dict)
    blocks: List[Dict[str, Any]] = field(default_factory=list)


def about_the_organizations(question: str) -> bool:
    return bool(_ABOUT_THE_ORGS.search(question or ""))


#: Added to the law prompt when the user turned on Brief answers.
BRIEF_LAW = ("\n\nBRIEF MODE: the user asked for a brief answer. Give the rule in at most 80 words: the direct "
             "answer and the key condition or figure, with citations. No step lists unless the question asks "
             "for steps.")


def answer(question: str, messages: List[Dict[str, Any]], brief: bool = False) -> Optional[LawAnswer]:
    """The knowledge-base answer, or None when this is not a law question the knowledge base
    answers confidently (the tool loop then handles it). Never raises."""
    if about_the_organizations(question):
        return None
    try:
        from gemini_brain.orchestrator.gemini_brain_runner import DIRECT_ANSWER_SYSTEM_PROMPT
        from gemini_brain.reasoning.bedrock_client import BedrockAdapter
        from gemini_brain.vat_kb.answer import tidy_answer
        from gemini_brain.vat_kb.augment import augment

        vat = augment(question, KNOWLEDGE_TYPE, DIRECT_ANSWER_SYSTEM_PROMPT)
        if not vat.model_id:
            return None
        system = vat.system + BRIEF_LAW if brief and isinstance(vat.system, str) else vat.system
        adapter = BedrockAdapter(vat.model_id, label="agent-law")
        text = adapter.converse(system, messages, max_tokens=vat.max_tokens, purpose="agent_law")
        return LawAnswer(answer=tidy_answer(text or ""), usage=adapter.get_token_usage(), blocks=vat.blocks)
    except Exception as e:  # noqa: BLE001 - the tool loop still answers
        logger.warning("agent law route failed, using the tool loop: %s", e)
        return None
