"""Query-time search over a VAT knowledge-base build (R5, R7).

Two rankings are combined with Reciprocal Rank Fusion: cosine similarity of bge-small
embeddings, and BM25 over the same chunk texts. Phase 0 measured the right document in the
top 6 for all 24 test questions with this hybrid alone; a cross-encoder reranker is optional
and off by default.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from gemini_brain.vat_kb.embedder import BgeSmallEmbedder, Embedder
from gemini_brain.vat_kb.documents import stale_passage_reason, superseded_reason
from gemini_brain.vat_kb.expansion import expand
from gemini_brain.vat_kb.index import MODELS_DIR, Chunk, current_build

POOL = 100               # candidates taken from each ranking before fusion
RRF_K = 60
TOP_K = 8                # Phase 1 check: 8 finds every expected source (6 missed 1 of 34)
RERANK_POOL = 20
KEEP_FUSED = 3           # these fused results survive reranking
MAX_PER_DOC = 4          # one long document must not fill every slot (it hid Cabinet Decision 153 behind VATP047)
# Neighbouring chunks added around the best chunk of the top documents: (documents, chunks each side).
# A list of amendments or conditions often runs on over the next chunks.
NEIGHBOURS = ((1, 2), (1, 1))   # top document ±2, second document ±1
OPENING_CHUNK_DOCS = 3          # documents whose opening chunk (scope, dates, summary) is always added
TOP_DOC_EXTRA_WINDOW = 1      # chunks either side of the best document's other matches
MAX_CONTEXT_CHARS = 16_000
# A search is "confident" on a strong meaning match, or a fair meaning match backed by a strong
# keyword match. Set from scripts/eval/vat_kb_retrieval_check.py (build 2026-10-04): the 34 VAT
# questions scored dense >= 0.72; general accounting and app questions scored 0.49-0.70 unless
# their wording overlaps VAT law ("overdue invoices", 0.74), which the Phase 2 detector rejects.
STRONG_DENSE = 0.75
MIN_DENSE = 0.70
MIN_BM25 = 15.0

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the and or of to in on for is are was be by with as at it this that from do does we our you your "
    "i my me can what how which who when where if any not there their they them has have had been will would "
    "should shall may such these those than then into its".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


@dataclass
class Hit:
    chunk: Chunk
    dense: float
    bm25: float
    fused_rank: int


@dataclass
class SearchResult:
    question: str
    expanded: str
    hits: list[Hit]
    confident: bool
    reason: str
    top_dense: float = 0.0     # best cosine similarity over all chunks
    top_bm25: float = 0.0      # best BM25 score over all chunks
    timings_ms: dict = field(default_factory=dict)

    def sources(self) -> list[Chunk]:
        """One entry per document, in first-appearance order: the numbering used for [1], [2] ..."""
        seen, out = set(), []
        for h in self.hits:
            if h.chunk.url not in seen:
                seen.add(h.chunk.url)
                out.append(h.chunk)
        return out


class KnowledgeBase:
    def __init__(self, build_dir: Path, embedder: Embedder | None = None, reranker=None, rerank: bool = False):
        self.build_dir = build_dir
        with open(build_dir / "chunks.jsonl", encoding="utf-8") as f:
            self.chunks = [Chunk(**json.loads(line)) for line in f]
        self.vectors = np.load(build_dir / "embeddings.npy")
        if len(self.vectors) != len(self.chunks):
            raise ValueError(f"{build_dir}: {len(self.chunks)} chunks but {len(self.vectors)} vectors")
        self.bm25 = BM25Okapi([tokenize(c.text) for c in self.chunks])
        self.embedder = embedder or BgeSmallEmbedder(build_dir.parent.parent / MODELS_DIR)
        self.rerank_enabled = rerank
        self._reranker = reranker
        self._by_doc: dict[str, list[int]] = {}
        for c in self.chunks:
            self._by_doc.setdefault(c.url, []).append(c.id)
        # Documents added to the superseded list (and stale passages) after this build was made are
        # skipped at query time, so updating the lists takes effect without rebuilding.
        self._blocked = np.array([bool(superseded_reason(c.title) or stale_passage_reason(c.text))
                                  for c in self.chunks])

    def search(self, question: str, k: int = TOP_K) -> SearchResult:
        t0 = time.perf_counter()
        query = expand(question)
        dense = self.vectors @ self.embedder.embed_query(query)
        t1 = time.perf_counter()
        sparse = self.bm25.get_scores(tokenize(query))
        if self._blocked.any():
            dense = np.where(self._blocked, -1.0, dense)
            sparse = np.where(self._blocked, 0.0, sparse)
        dense_rank = list(np.argsort(-dense)[:POOL])
        sparse_rank = [i for i in np.argsort(-sparse)[:POOL] if sparse[i] > 0]
        fused = rrf([dense_rank, sparse_rank])
        t2 = time.perf_counter()

        chosen = self._rerank(query, fused, k) if self.rerank_enabled else self._select(fused, k)
        # Each method's best match is always kept: an exact phrase ("wholly zero-rated") can be
        # keyword search's #1 while meaning search ranks it ~70th, which drops it out of the fusion.
        for best in (sparse_rank[:1] + dense_rank[:1]):
            if best not in chosen:
                chosen.append(best)
        chosen = self._with_neighbours(chosen)
        hits, used = [], 0
        for idx in chosen:
            c = self.chunks[idx]
            if hits and used + len(c.text) > MAX_CONTEXT_CHARS:
                break
            used += len(c.text)
            hits.append(Hit(c, float(dense[idx]), float(sparse[idx]), fused.index(idx) + 1 if idx in fused else -1))

        top_dense = float(dense[dense_rank[0]]) if dense_rank else 0.0
        top_sparse = float(sparse[sparse_rank[0]]) if sparse_rank else 0.0
        confident = top_dense >= STRONG_DENSE or (top_dense >= MIN_DENSE and top_sparse >= MIN_BM25)
        reason = "" if confident else f"weak match (dense {top_dense:.2f}, bm25 {top_sparse:.1f})"
        return SearchResult(question, query, hits if confident else [], confident, reason, top_dense, top_sparse, {
            "embed": round((t1 - t0) * 1000), "rank": round((t2 - t1) * 1000),
            "select": round((time.perf_counter() - t2) * 1000)})

    def _rerank(self, query: str, fused: list[int], k: int) -> list[int]:
        if self._reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            self._reranker = TextCrossEncoder("BAAI/bge-reranker-base",
                                              cache_dir=str(self.build_dir.parent.parent / MODELS_DIR))
        pool = fused[:RERANK_POOL]
        scores = list(self._reranker.rerank(query, [self.chunks[i].text for i in pool]))
        reranked = [pool[i] for i in np.argsort(scores)[::-1]]
        keep = fused[:KEEP_FUSED]
        return keep + [i for i in reranked if i not in keep][: max(0, k - len(keep))]

    def _select(self, fused: list[int], k: int) -> list[int]:
        """Best k fused results, at most MAX_PER_DOC from any one document."""
        chosen, per_doc = [], {}
        for idx in fused:
            url = self.chunks[idx].url
            if per_doc.get(url, 0) >= MAX_PER_DOC:
                continue
            per_doc[url] = per_doc.get(url, 0) + 1
            chosen.append(idx)
            if len(chosen) == k:
                break
        return chosen

    def _with_neighbours(self, chosen: list[int]) -> list[int]:
        """Append the chunks around the best chunk of the top documents (see NEIGHBOURS): the deciding
        sentence is often one or two chunks away from the one that matched. They go after the ranked
        results, so they never push a better match out (and add no new source numbers)."""
        out, docs_done = list(chosen), []
        # The opening chunk of an FTA document states which law it concerns, its scope, the date it
        # applies from and (for clarifications) the summary of the rule — VATP047's "with effect from
        # 14 January 2026", VATP027's 2021 designated-zone relief. Include it for the top documents.
        tops = []
        for idx in chosen:
            if self.chunks[idx].url not in tops:
                tops.append(self.chunks[idx].url)
            if len(tops) == OPENING_CHUNK_DOCS:
                break
        for url in tops:
            first = self._by_doc[url][0]
            if first not in out and not self._blocked[first]:
                out.append(first)
        windows = [w for count, w in NEIGHBOURS for _ in range(count)]
        for idx in chosen:
            if len(docs_done) >= len(windows):
                break
            url = self.chunks[idx].url
            if url in docs_done:
                continue
            window = windows[len(docs_done)]
            docs_done.append(url)
            ids = self._by_doc[url]
            pos = ids.index(idx)
            for step in range(1, window + 1):
                for n in (pos - step, pos + step):
                    if 0 <= n < len(ids) and ids[n] not in out and not self._blocked[ids[n]]:
                        out.append(ids[n])
            if len(docs_done) == len(windows):
                break
        # The best document often matches in two places (VATP046: its summary list and the section on
        # the 2025 law); the next chunk after each of its other matches finishes that section.
        if tops:
            ids = self._by_doc[tops[0]]
            for idx in [i for i in chosen if self.chunks[i].url == tops[0]][1:]:
                pos = ids.index(idx)
                for n in range(pos - TOP_DOC_EXTRA_WINDOW, pos + TOP_DOC_EXTRA_WINDOW + 1):
                    if 0 <= n < len(ids) and ids[n] not in out and not self._blocked[ids[n]]:
                        out.append(ids[n])
        # A match from a document that got no window can start mid-sentence ("...Entity shall appoint
        # ... by 31 March 2027" with "Government" cut off); its previous chunk holds the start. It goes
        # right before the match, so the context cap keeps or drops the two together.
        for idx in chosen:
            url = self.chunks[idx].url
            if url in docs_done or (tops and url == tops[0]):
                continue
            ids = self._by_doc[url]
            pos = ids.index(idx)
            if pos > 0 and ids[pos - 1] not in out and not self._blocked[ids[pos - 1]]:
                out.insert(out.index(idx), ids[pos - 1])
        return out


_cache: dict[Path, KnowledgeBase] = {}
_cache_lock = threading.Lock()


def load_knowledge_base(kb_dir: Path | str, rerank: bool = False) -> KnowledgeBase | None:
    """The knowledge base for the build named in CURRENT, loaded once per build. None if there is no build."""
    build = current_build(Path(kb_dir))
    if build is None:
        return None
    with _cache_lock:
        kb = _cache.get(build)
        if kb is None:
            kb = KnowledgeBase(build, rerank=rerank)
            _cache.clear()           # a new build replaces the old one in memory
            _cache[build] = kb
        return kb
