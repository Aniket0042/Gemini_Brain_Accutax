"""Sentence embeddings for the VAT knowledge base: BAAI/bge-small-en-v1.5 (MIT) run locally
through fastembed's ONNX runtime. No PyTorch, no per-query cost."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
# bge v1.5 is trained with this instruction on the query side only; passages are embedded as-is.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


class BgeSmallEmbedder:
    name = MODEL_NAME

    def __init__(self, cache_dir: Path | None = None):
        self._cache_dir = str(cache_dir) if cache_dir else None
        self._model = None
        self._lock = threading.Lock()

    def _get(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding
                    self._model = TextEmbedding(MODEL_NAME, cache_dir=self._cache_dir)
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        vectors = np.asarray(list(self._get().embed(list(texts), batch_size=32)), dtype="float32")
        return normalise(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        vector = np.asarray(next(iter(self._get().embed([QUERY_INSTRUCTION + text]))), dtype="float32")
        return normalise(vector)
