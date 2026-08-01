from app.services.retrieval import reranker_retrieval_config


def test_returns_config_when_model_set():
    assert reranker_retrieval_config("cohere/rerank-english-v3.0", 20) == {
        "reranker": {"model": "cohere/rerank-english-v3.0", "candidate_count": 20}
    }


def test_returns_none_when_model_empty():
    assert reranker_retrieval_config("", 20) is None
