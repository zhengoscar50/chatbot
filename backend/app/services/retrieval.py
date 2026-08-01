from __future__ import annotations


def reranker_retrieval_config(model: str, candidate_count: int) -> dict | None:
    """Build a KB retrieval_config with a reranker, or None if disabled."""
    if not model:
        return None
    return {"reranker": {"model": model, "candidate_count": candidate_count}}
