"""Monthly refresh of the VAT knowledge base: fetch the FTA documents again, rebuild, check, keep or roll back.

    python scripts/vat_kb_refresh.py                  # the knowledge base in VAT_KB_DIR
    python scripts/vat_kb_refresh.py --dir COPY       # a copy, to try it without touching the one in use
    python scripts/vat_kb_refresh.py --no-crawl       # rebuild from the PDFs already downloaded

Steps: remember the build in use and its document list; crawl the FTA site (new and changed documents); build a new
index (the build switches CURRENT itself when it completes); check the new build; if a check fails, point CURRENT back
at the previous build. The running app picks up a new CURRENT on its next search, so no restart is needed.

Checks: the new build has at least 90% of the chunks of the previous one, and each of a few fixed questions finds a
confident match. The report lists the FTA documents that are new since the last crawl, so a person can read what changed.
One line per run goes to <dir>/refresh_log.jsonl and to the audit log. Exit code 0 when the new build is in use, 1 when
it was rolled back or could not be built.

Run by the systemd timer in deploy/vat-kb-refresh.timer (monthly) as the app user.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

MIN_CHUNK_SHARE = 0.9
SMOKE_QUESTIONS = (
    "What is the standard rate of VAT in the UAE?",
    "What is the mandatory VAT registration threshold?",
    "What must a tax invoice contain?",
    "When is the VAT return due?",
    "Which supplies are zero-rated?",
)


def _titles(kb_dir: Path) -> set:
    from gemini_brain.vat_kb.documents import read_documents
    from gemini_brain.vat_kb.index import DOCUMENTS_FILE
    path = kb_dir / DOCUMENTS_FILE
    return {d.title for d in read_documents(path)} if path.exists() else set()


def _chunks(build: Path | None) -> int:
    try:
        return int(json.loads((build / "build_info.json").read_text(encoding="utf-8"))["chunks"]) if build else 0
    except (OSError, ValueError, KeyError):
        return 0


def check_build(kb_dir: Path, previous_chunks: int) -> List[str]:
    """What is wrong with the build in CURRENT; [] when it can be used."""
    from gemini_brain.vat_kb.index import current_build
    from gemini_brain.vat_kb.retrieval import load_knowledge_base
    problems = []
    build = current_build(kb_dir)
    chunks = _chunks(build)
    if previous_chunks and chunks < MIN_CHUNK_SHARE * previous_chunks:
        problems.append(f"the new build has {chunks} chunks, under {MIN_CHUNK_SHARE:.0%} of the previous {previous_chunks}")
    kb = load_knowledge_base(kb_dir)
    if kb is None:
        return problems + ["the new build cannot be loaded"]
    for q in SMOKE_QUESTIONS:
        if not kb.search(q).confident:
            problems.append(f"no confident match for: {q}")
    return problems


def refresh(kb_dir: Path, crawl: bool = True) -> Dict[str, Any]:
    from gemini_brain.vat_kb.index import CURRENT_FILE, current_build
    started = dt.datetime.now(dt.timezone.utc)
    before = current_build(kb_dir)
    before_name = before.name if before else None
    before_chunks = _chunks(before)
    before_titles = _titles(kb_dir)
    report: Dict[str, Any] = {"started_utc": started.isoformat(timespec="seconds"), "dir": str(kb_dir),
                              "previous_build": before_name, "previous_chunks": before_chunks}
    try:
        if crawl:
            from gemini_brain.vat_kb.documents import write_documents
            from gemini_brain.vat_kb.fta_crawler import collect, download
            from gemini_brain.vat_kb.index import DOCUMENTS_FILE, PDF_DIR
            docs = collect()
            if before_titles and len(docs) < MIN_CHUNK_SHARE * len(before_titles):
                raise RuntimeError(f"the FTA list has {len(docs)} documents, under {MIN_CHUNK_SHARE:.0%} of the previous "
                                   f"{len(before_titles)}: the site may have changed; nothing was replaced")
            write_documents(kb_dir / DOCUMENTS_FILE, docs)
            failures = download(docs, kb_dir / PDF_DIR)
            report["download_failures"] = [f"{d.title}: {e}" for d, e in failures][:20]
        from gemini_brain.vat_kb.embedder import BgeSmallEmbedder
        from gemini_brain.vat_kb.index import MODELS_DIR, build
        new = build(kb_dir, BgeSmallEmbedder(kb_dir / MODELS_DIR))
        report.update(new_build=new.name, new_chunks=_chunks(new))
        problems = check_build(kb_dir, before_chunks)
    except Exception as e:  # noqa: BLE001 - a failed refresh keeps the build in use
        problems = [f"{type(e).__name__}: {e}"]
    report["new_documents"] = sorted(_titles(kb_dir) - before_titles)[:100]
    report["problems"] = problems
    if problems and before_name and (current_build(kb_dir) is None or current_build(kb_dir).name != before_name):
        (kb_dir / CURRENT_FILE).write_text(before_name, encoding="utf-8")
        report["rolled_back_to"] = before_name
    report["in_use"] = current_build(kb_dir).name if current_build(kb_dir) else None
    report["ok"] = not problems
    report["seconds"] = round((dt.datetime.now(dt.timezone.utc) - started).total_seconds())
    with open(kb_dir / "refresh_log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(report, ensure_ascii=False) + "\n")
    try:
        from gemini_brain.agent import audit
        audit.event("vat_kb_refresh", ok=report["ok"], in_use=report["in_use"], new_documents=len(report["new_documents"]),
                    problems=problems[:5])
    except Exception:  # noqa: BLE001
        pass
    return report


def main() -> int:
    from gemini_brain.config.settings import settings
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=None, help="knowledge-base directory (default: VAT_KB_DIR)")
    ap.add_argument("--no-crawl", action="store_true", help="rebuild from the PDFs already downloaded")
    args = ap.parse_args()
    kb_dir = args.dir or (Path(settings.vat_kb_dir) if settings.vat_kb_dir else None)
    if kb_dir is None or not kb_dir.is_dir():
        print("no knowledge-base directory: set VAT_KB_DIR or pass --dir")
        return 1
    report = refresh(kb_dir, crawl=not args.no_crawl)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
