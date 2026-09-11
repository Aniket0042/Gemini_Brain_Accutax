"""
redactor.py — Presidio-based PII redactor for user queries.

Redacts Personally Identifiable Information (PII) from user queries before
they reach third-party AI providers (Google Gemini or AWS Bedrock/Claude).

Supported Entity Recognizers:
  - Email Address (built-in EMAIL_ADDRESS) -> [EMAIL_REDACTED]
  - Phone Number (built-in PHONE_NUMBER + custom UAE_PHONE_NUMBER) -> [PHONE_REDACTED]
  - Credit/Debit Card (built-in CREDIT_CARD, Luhn-validated) -> [CARD_REDACTED]
  - IBAN Code (built-in IBAN_CODE + custom UAE_IBAN_CODE) -> [IBAN_REDACTED]
  - UAE Emirates ID (custom UAE_EMIRATES_ID) -> [ID_REDACTED]

Passport recognizer omitted for v1 per high false-positive risk design decision.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

logger = logging.getLogger("gemini_brain.pii.redactor")

# ── Custom Recognizer Definitions ───────────────────────────────────────────

# UAE Emirates ID: 784-YYYY-XXXXXXX-Z (e.g. 784-1990-1234567-1)
EMIRATES_ID_PATTERN = Pattern(
    name="emirates_id_pattern",
    regex=r"\b784-\d{4}-\d{7}-\d\b",
    score=0.95,
)
EMIRATES_ID_RECOGNIZER = PatternRecognizer(
    supported_entity="UAE_EMIRATES_ID",
    patterns=[EMIRATES_ID_PATTERN],
)

# UAE Phone Numbers: local (05X XXX XXXX, 05X-XXX-XXXX, 05XXXXXXXX) or intl (+971 5X XXX XXXX)
UAE_PHONE_PATTERN = Pattern(
    name="uae_phone_pattern",
    regex=r"(?:^|\b)(?:\+971|0)\s?5[02456789](?:[\s-]?\d{3})(?:[\s-]?\d{4})\b",
    score=0.85,
)
UAE_PHONE_RECOGNIZER = PatternRecognizer(
    supported_entity="UAE_PHONE_NUMBER",
    patterns=[UAE_PHONE_PATTERN],
)

# UAE IBAN: AE followed by 21 digits (23 characters total e.g. AE070330000000000001234)
UAE_IBAN_PATTERN = Pattern(
    name="uae_iban_pattern",
    regex=r"\bAE\d{21}\b",
    score=0.90,
)
UAE_IBAN_RECOGNIZER = PatternRecognizer(
    supported_entity="UAE_IBAN_CODE",
    patterns=[UAE_IBAN_PATTERN],
)

# ── Operator Configurations for Anonymization ───────────────────────────────

OPERATORS: Dict[str, OperatorConfig] = {
    "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "[EMAIL_REDACTED]"}),
    "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "[PHONE_REDACTED]"}),
    "UAE_PHONE_NUMBER": OperatorConfig("replace", {"new_value": "[PHONE_REDACTED]"}),
    "CREDIT_CARD": OperatorConfig("replace", {"new_value": "[CARD_REDACTED]"}),
    "IBAN_CODE": OperatorConfig("replace", {"new_value": "[IBAN_REDACTED]"}),
    "UAE_IBAN_CODE": OperatorConfig("replace", {"new_value": "[IBAN_REDACTED]"}),
    "UAE_EMIRATES_ID": OperatorConfig("replace", {"new_value": "[ID_REDACTED]"}),
}

ENTITIES_TO_ANALYZE: List[str] = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "UAE_PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "UAE_IBAN_CODE",
    "UAE_EMIRATES_ID",
]


class PIIRedactor:
    """PII Redactor wrapping Presidio AnalyzerEngine and AnonymizerEngine."""

    def __init__(self, score_threshold: float = 0.35) -> None:
        self.score_threshold = score_threshold
        
        # Explicitly configure NlpEngineProvider to use lightweight en_core_web_sm
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
        )
        nlp_engine = provider.create_engine()
        
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        self.anonymizer = AnonymizerEngine()

        # Register custom recognizers
        self.analyzer.registry.add_recognizer(EMIRATES_ID_RECOGNIZER)
        self.analyzer.registry.add_recognizer(UAE_PHONE_RECOGNIZER)
        self.analyzer.registry.add_recognizer(UAE_IBAN_RECOGNIZER)

    def redact(self, text: str) -> Tuple[str, Dict[str, int]]:
        """Analyze and redact PII from input text.

        Parameters
        ----------
        text : str
            Raw user query string.

        Returns
        -------
        Tuple[str, Dict[str, int]]
            (redacted_text, count_by_entity_type)
            Count dictionary keys correspond to canonical entity names:
            EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, IBAN_CODE, UAE_EMIRATES_ID.
        """
        if not text:
            return "", {}

        results = self.analyzer.analyze(
            text=text,
            entities=ENTITIES_TO_ANALYZE,
            language="en",
            score_threshold=self.score_threshold,
        )

        if not results:
            return text, {
                "EMAIL_ADDRESS": 0,
                "PHONE_NUMBER": 0,
                "CREDIT_CARD": 0,
                "IBAN_CODE": 0,
                "UAE_EMIRATES_ID": 0,
            }

        anonymized = self.anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=OPERATORS,
        )

        counts: Dict[str, int] = {
            "EMAIL_ADDRESS": 0,
            "PHONE_NUMBER": 0,
            "CREDIT_CARD": 0,
            "IBAN_CODE": 0,
            "UAE_EMIRATES_ID": 0,
        }

        for res in results:
            entity = res.entity_type
            if entity == "UAE_PHONE_NUMBER":
                entity = "PHONE_NUMBER"
            elif entity == "UAE_IBAN_CODE":
                entity = "IBAN_CODE"

            if entity in counts:
                counts[entity] += 1
            else:
                counts[entity] = 1

        logger.info(
            "PII redaction completed. Redaction counts: %s",
            counts,
        )
        return anonymized.text, counts


# Global singleton instance for efficient reuse
_redactor_instance: PIIRedactor | None = None


def get_redactor() -> PIIRedactor:
    """Get or initialize singleton PIIRedactor instance."""
    global _redactor_instance
    if _redactor_instance is None:
        _redactor_instance = PIIRedactor()
    return _redactor_instance


def redact_pii(text: str) -> Tuple[str, Dict[str, int]]:
    """Redact PII from user query text.

    Parameters
    ----------
    text : str
        User query text.

    Returns
    -------
    Tuple[str, Dict[str, int]]
        (redacted_text, redaction_counts)
    """
    return get_redactor().redact(text)
