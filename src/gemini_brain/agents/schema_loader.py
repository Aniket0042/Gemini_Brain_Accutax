"""
Schema Loader — reads accutax_bk_table_description.json and builds a compact,
token-efficient schema string to inject into Claude's system prompt.

This prevents Claude from hallucinating table or column names.
The schema is loaded ONCE at import time and cached.
"""

import json
import os
import logging

logger = logging.getLogger("agents.schema_loader")

# Bundled with the package at gemini_brain/agents/data/
_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "accutax_bk_table_description.json",
)
_FALLBACK_PATH = "/root/accutax_bk_table_description.json"

# Type abbreviation map — keeps the prompt compact
_TYPE_MAP = {
    "character varying": "varchar",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "double precision": "float",
    "integer": "int",
    "bigint": "bigint",
    "smallint": "int",
    "boolean": "bool",
    "numeric": "numeric",
    "text": "text",
    "date": "date",
    "json": "json",
    "jsonb": "jsonb",
    "USER-DEFINED": "enum",
}


def _abbreviate(data_type: str) -> str:
    return _TYPE_MAP.get(data_type, data_type)


def _load_json() -> dict:
    for path in [_JSON_PATH, _FALLBACK_PATH]:
        try:
            with open(path) as f:
                data = json.load(f)
                logger.info(f"Loaded accutax_bk schema from {path} ({len(data)} tables)")
                return data
        except FileNotFoundError:
            continue
    logger.warning("accutax_bk_table_description.json not found — schema grounding disabled")
    return {}


def build_schema_block() -> str:
    """
    Returns a compact multi-line string listing every real table and column,
    suitable for injection into a system prompt.

    Format per table:
      table_name: col1(type) col2(type) col3(type?) ...
      (? = nullable)

    Kept compact on purpose — one line per table so Claude can scan quickly.
    """
    schema = _load_json()
    if not schema:
        return ""

    lines = []
    lines.append("## REAL DATABASE TABLES & COLUMNS (accutax_bk — authoritative)")
    lines.append("# RULE: ONLY use table/column names listed below. Never invent names.")
    lines.append("")

    for table, columns in sorted(schema.items()):
        parts = []
        for col in columns:
            typ = _abbreviate(col["data_type"])
            nullable = col.get("nullable", "YES")
            suffix = "?" if nullable == "YES" else ""
            parts.append(f"{col['column']}({typ}{suffix})")
        lines.append(f"  {table}: {', '.join(parts)}")

    lines.append("")
    lines.append("# END OF SCHEMA — never reference tables or columns not listed above.")
    return "\n".join(lines)


# Build once at import time
SCHEMA_BLOCK: str = build_schema_block()
