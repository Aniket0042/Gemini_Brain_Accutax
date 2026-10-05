"""UAE VAT knowledge base: official FTA documents, searchable for cited VAT-law answers.

Spec: docs/product/VAT_KNOWLEDGE_BASE_PLAN.md.

Offline (monthly):  python -m gemini_brain.vat_kb crawl  --dir <vat_kb_dir>
                    python -m gemini_brain.vat_kb build  --dir <vat_kb_dir>
Query time:         gemini_brain.vat_kb.augment.augment(...) from the runner, behind
                    settings.vat_kb_enabled (off by default).

This package imports nothing heavy at import time: the runner imports `augment`, which only
loads numpy / rank_bm25 / fastembed once the feature is switched on. Installs without the
optional `vat_kb` dependencies keep working unchanged.
"""
