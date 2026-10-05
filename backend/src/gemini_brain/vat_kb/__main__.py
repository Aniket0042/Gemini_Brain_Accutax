"""Command line for the VAT knowledge base.

    python -m gemini_brain.vat_kb crawl  --dir D   collect the FTA document list, download core PDFs
    python -m gemini_brain.vat_kb build  --dir D   build a new search index and switch to it
    python -m gemini_brain.vat_kb search --dir D "question"
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m gemini_brain.vat_kb")
    p.add_argument("command", choices=["crawl", "build", "search"])
    p.add_argument("--dir", required=True, type=Path, help="knowledge-base directory")
    p.add_argument("--rerank", action="store_true", help="search: use the cross-encoder reranker")
    p.add_argument("question", nargs="?")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "crawl":
        from gemini_brain.vat_kb.documents import write_documents
        from gemini_brain.vat_kb.fta_crawler import collect, download
        from gemini_brain.vat_kb.index import DOCUMENTS_FILE, PDF_DIR
        docs = collect()
        write_documents(args.dir / DOCUMENTS_FILE, docs)
        print("tiers:", dict(Counter(d.tier for d in docs)))
        failures = download(docs, args.dir / PDF_DIR)
        for doc, error in failures:
            print(f"FAILED {doc.title}: {error}")
        return 1 if failures else 0

    if args.command == "build":
        from gemini_brain.vat_kb.embedder import BgeSmallEmbedder
        from gemini_brain.vat_kb.index import MODELS_DIR, build
        print("build:", build(args.dir, BgeSmallEmbedder(args.dir / MODELS_DIR)))
        return 0

    from gemini_brain.vat_kb.retrieval import load_knowledge_base
    if not args.question:
        p.error("search needs a question")
    kb = load_knowledge_base(args.dir, rerank=args.rerank)
    if kb is None:
        print("no build found; run `build` first")
        return 1
    result = kb.search(args.question)
    print(f"expanded: {result.expanded}\nconfident: {result.confident} {result.reason}\ntimings: {result.timings_ms}")
    for n, h in enumerate(result.hits, 1):
        print(f"{n}. [{h.dense:.2f} / {h.bm25:.1f} / #{h.fused_rank}] {h.chunk.title} (part {h.chunk.position})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
