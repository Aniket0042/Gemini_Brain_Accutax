# Multi-Agent Financial Intelligence System
# ==========================================
#
# Architecture:
#
#   Coordinator Agent (Claude Sonnet via Bedrock tool-calling)
#       ├── Schema Agent    — DB introspection, table/column resolution
#       ├── Finance Agent   — Invoice data, financial metrics, SQL execution
#       ├── Tax Agent       — Middle East VAT regimes (UAE 5%, KSA 15%)
#       └── Reasoning Agent — Cross-result comparison, narrative synthesis
#
# All agents communicate via structured JSON tool calls.
# The Coordinator never touches SQL or computes numbers directly.
#
# Migrated from the original query-parser-bedrock_clean monolith into this
# package so it deploys as part of gemini_brain instead of depending on a
# path that only existed on one developer's machine.
