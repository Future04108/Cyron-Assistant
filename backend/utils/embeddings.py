"""Embeddings utility using sentence-transformers."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: "SentenceTransformer | None" = None
EMBEDDING_DIM = 384


def get_embedding_model() -> "SentenceTransformer":
    """Lazy-load and return the embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_text(text: str) -> list[float]:
    """Get embedding vector for text."""
    model = get_embedding_model()
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


def warmup_embeddings() -> None:
    """Warm up embedding model and first inference.

    This removes the cold-start delay on the first knowledge write request.
    """
    model = get_embedding_model()
    # Tiny inference to initialize model internals/JIT/caches.
    model.encode("warmup", convert_to_numpy=True)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    import numpy as np
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))
