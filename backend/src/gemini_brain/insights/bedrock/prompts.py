SYSTEM_PROMPT = """You are AccuTax AI, a financial insight assistant embedded in an accounting
dashboard. You are given a set of precomputed facts about one dashboard widget for a specific
organization. These facts are already correct and final -- you must not recalculate, round
differently, or invent any number that is not explicitly present in the facts.

Your job:
1. Write a one-sentence "summary" that states the most important figure(s) from the facts
   (e.g. revenue growth percentage and/or profit margin), in plain business language.
2. Write 2 to 4 short "insights", each one either a "warning" (a risk or concerning ratio),
   a "tip" (an actionable recommendation), or an "observation" (a neutral but useful pattern).
   Each insight must be grounded only in the given facts -- do not fabricate customer names,
   dates, or figures that were not provided.

Keep language concise, professional, and encouraging where appropriate -- like a sharp financial
analyst giving a 10-second briefing, not a generic chatbot. Do not repeat the summary sentence
verbatim inside the insights. Respond only by calling the provided tool."""


TOOL_NAME = "provide_insight"

TOOL_SPEC = {
    "toolSpec": {
        "name": TOOL_NAME,
        "description": "Return the generated financial insight narrative for this widget.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "One sentence summarizing the key figures.",
                    },
                    "insights": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["warning", "tip", "observation"],
                                },
                                "text": {"type": "string"},
                            },
                            "required": ["type", "text"],
                        },
                    },
                },
                "required": ["summary", "insights"],
            }
        },
    }
}


def build_user_message(widget_display_name: str, facts_text: str) -> str:
    return (
        f"Widget: {widget_display_name}\n\n"
        f"Facts (use only these numbers, do not recalculate):\n{facts_text}\n\n"
        "Now call the tool with your summary and insights."
    )
