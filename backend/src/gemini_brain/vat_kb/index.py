"""Build the searchable VAT knowledge base from downloaded FTA PDFs.

Layout under the knowledge-base directory:

    documents.csv          FTA document list with tiers (written by `crawl`)
    pdfs/                  downloaded PDFs
    builds/<timestamp>/    chunks.jsonl, embeddings.npy, build_info.json (one immutable build)
    CURRENT                name of the build in use; changed only after a build completes
    models/                embedding model cache

A plain CURRENT file is used instead of a symlink so the same layout works on Windows dev
machines and the Linux VM.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from gemini_brain.vat_kb.documents import CORE, read_documents, superseded_reason
from gemini_brain.vat_kb.embedder import Embedder
from gemini_brain.vat_kb.text import chunk, clean, effective_date, header, pdf_text

log = logging.getLogger("gemini_brain.vat_kb.index")

DOCUMENTS_FILE = "documents.csv"
PDF_DIR = "pdfs"
BUILDS_DIR = "builds"
CURRENT_FILE = "CURRENT"
MODELS_DIR = "models"


@dataclass
class Chunk:
    id: int
    position: int          # order within its document, for neighbour lookup
    title: str
    category: str
    doc_type: str          # statute | guidance
    issue_date: str
    effective_date: str
    url: str
    text: str              # header line + chunk text


def current_build(kb_dir: Path) -> Path | None:
    marker = kb_dir / CURRENT_FILE
    if not marker.exists():
        return None
    build = kb_dir / BUILDS_DIR / marker.read_text(encoding="utf-8").strip()
    return build if build.is_dir() else None


def build(kb_dir: Path, embedder: Embedder, keep_builds: int = 3) -> Path:
    """Extract, chunk and embed every core, non-superseded document that has a PDF, write a new
    build, then point CURRENT at it. The previous builds stay for rollback."""
    started = time.time()
    docs = [d for d in read_documents(kb_dir / DOCUMENTS_FILE)
            if d.tier == CORE and not superseded_reason(d.title)]
    chunks: list[Chunk] = []
    missing, empty = [], []
    for doc in docs:
        pdf = kb_dir / PDF_DIR / doc.file
        if not pdf.exists():
            missing.append(doc.file)
            continue
        text = clean(pdf_text(pdf))
        if len(text) < 200:
            empty.append(doc.file)
            continue
        # A public clarification's first "with effect from" is usually the date of the law it
        # explains, not its own; only laws and decisions get an effective date in the header.
        effective = effective_date(text) if doc.doc_type == "statute" else ""
        top = header(doc.title, doc.issue_date, effective)
        for position, piece in enumerate(chunk(text)):
            chunks.append(Chunk(id=len(chunks), position=position, title=doc.title, category=doc.category,
                                doc_type=doc.doc_type, issue_date=doc.issue_date, effective_date=effective,
                                url=doc.url, text=f"{top}\n{piece}"))
    if not chunks:
        raise RuntimeError(f"no chunks built from {kb_dir / PDF_DIR}; run `crawl` first")

    log.info("embedding %d chunks from %d documents", len(chunks), len({c.url for c in chunks}))
    vectors = embedder.embed_documents([c.text for c in chunks])

    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    builds = kb_dir / BUILDS_DIR
    staging = builds / f".{name}.tmp"
    staging.mkdir(parents=True, exist_ok=True)
    with open(staging / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    np.save(staging / "embeddings.npy", vectors.astype("float32"))
    info = {"created_utc": name, "embedding_model": embedder.name, "documents": len({c.url for c in chunks}),
            "chunks": len(chunks), "missing_pdfs": missing, "unreadable_pdfs": empty,
            "seconds": round(time.time() - started)}
    (staging / "build_info.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    final = builds / name
    staging.rename(final)
    (kb_dir / CURRENT_FILE).write_text(name, encoding="utf-8")

    for old in sorted(p for p in builds.iterdir() if p.is_dir() and not p.name.startswith("."))[:-keep_builds]:
        shutil.rmtree(old, ignore_errors=True)
    log.info("build %s ready: %s", name, info)
    return final
