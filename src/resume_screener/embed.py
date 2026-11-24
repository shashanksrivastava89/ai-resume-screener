"""Embeddings + cosine similarity (P7).

Why this module exists
----------------------
The `domain_transfer_distance` signal needs a transparent, reproducible number for
"how close is this resume's domain to the JD". We embed both texts with the Azure
OpenAI embedding deployment and compare them with cosine similarity. Declaring it
as a capped signal (instead of blending similarity into the score invisibly) is a
core design decision — see ARCHITECTURE-TARGET-DESIGN and CLAUDE.md §1.

How it stays cheap and reproducible
-----------------------------------
- Every text's vector is cached in .cache/embed/, keyed by hash(text + model).
  A 500-resume run embeds each unique text exactly once; re-runs cost nothing.
- Cache misses are sent in BATCHED API calls (the API accepts a list of inputs),
  never one call per text.
- temperature does not apply to embeddings; same text + same model = same vector.
"""

from __future__ import annotations

import numpy as np

from resume_screener.azure_clients import llm_retry
from resume_screener.cache import JsonCache, content_hash

# The embedding API rejects over-long inputs (~8k tokens). Resumes/JDs here are a
# few thousand characters, so a generous character guard is enough — we truncate
# rather than fail, because a slightly-truncated resume still embeds fine.
MAX_EMBED_CHARS = 20_000

# How many texts to send per API call. The API allows larger batches, but smaller
# chunks keep any single retry cheap.
BATCH_SIZE = 128


@llm_retry  # same transient-error retry policy (429/5xx/timeouts) as chat calls
def _create_embeddings(client, model: str, inputs: list[str]):
    from resume_screener.azure_clients import METER, with_auth_recovery

    # A key rotated in Key Vault mid-run: re-read once instead of failing the run.
    response = with_auth_recovery(lambda: client.embeddings.create(model=model, input=inputs))
    METER.add_embeddings(getattr(response, "usage", None))  # cost-estimate feed
    return response


def embed_texts(
    texts: list[str], client, model: str, cache: JsonCache, batch_size: int = BATCH_SIZE
) -> list[list[float]]:
    """Return one vector per input text, in input order. Cache-first, then batched.

    The cache key includes the model (deployment) name, so switching embedding
    models never serves stale vectors.
    """
    texts = [t[:MAX_EMBED_CHARS] for t in texts]
    keys = [content_hash(t, model) for t in texts]

    vectors: dict[int, list[float]] = {}
    misses: list[int] = []
    for i, key in enumerate(keys):
        hit = cache.get("embed", key)
        if hit is not None:
            vectors[i] = hit
        else:
            misses.append(i)

    # One API call per chunk of misses (not per text — that's the §5 cost model).
    for chunk_start in range(0, len(misses), batch_size):
        chunk = misses[chunk_start : chunk_start + batch_size]
        response = _create_embeddings(client, model, [texts[i] for i in chunk])
        # response.data is index-aligned with the input list; we still read
        # datum.index instead of assuming order — the API contract, not luck.
        for datum in response.data:
            original_index = chunk[datum.index]
            vectors[original_index] = list(datum.embedding)
            cache.set("embed", keys[original_index], vectors[original_index])

    return [vectors[i] for i in range(len(texts))]


def cosine(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity. Returns 0.0 for zero vectors instead of NaN."""
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denominator)


def clamped_cosine01(a: list[float], b: list[float]) -> float:
    """Cosine clamped into 0..1 — the contract for FeatureSet.domain_fit_cosine.

    Embedding cosines are practically non-negative for natural-language pairs;
    the clamp just guarantees the documented range for downstream consumers.
    """
    return max(0.0, min(1.0, cosine(a, b)))
