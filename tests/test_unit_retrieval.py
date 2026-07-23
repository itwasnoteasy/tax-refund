"""Layer 1 -- hybrid retrieval (dense + BM25) and RRF fusion, against
the seeded FAQ corpus, with known expected top results for sample
queries. No network: the local hash embedding client and the lexical
fallback reranker are used throughout (no GEMINI_API_KEY/GOOGLE_API_KEY
in this environment -- see docs/spec/07_TESTING_STRATEGY.md).

Confidence-floor tests use an injected fake reranker rather than the
real credential-gated one, so the short-circuit behavior itself is
tested deterministically, independent of whichever reranker happens to
be selected by environment.
"""
import pytest

from app.help_assistant import retrieval
from app.help_assistant.faq_corpus import FAQ_CORPUS
from app.help_assistant.retrieval import RankedDocument

_KNOWN_QUERIES = [
    ("What is form 1040-X used for?", "faq-1040x"),
    ("Why do I need to verify my identity with the IRS?", "faq-identity-verification"),
    (
        "The IRS says my return is still processing -- what does that mean?",
        "faq-still-processing",
    ),
    (
        "Why is my refund delayed because of the earned income tax credit and PATH Act?",
        "faq-path-act",
    ),
    (
        "Can I get my direct deposit account changed after I already filed?",
        "faq-change-direct-deposit",
    ),
    ("How do I get a copy of my tax transcript?", "faq-tax-transcript"),
    ("What happens if I file my taxes after the deadline?", "faq-missed-deadline"),
]

_ALL_DOC_IDS = {doc.doc_id for doc in FAQ_CORPUS}


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    retrieval.reset_corpus_embeddings_cache()
    retrieval.reset_bm25_index_cache()
    retrieval.reset_default_embedding_client_cache()
    yield
    retrieval.reset_corpus_embeddings_cache()
    retrieval.reset_bm25_index_cache()
    retrieval.reset_default_embedding_client_cache()


def test_faq_corpus_is_within_the_spec_size_range() -> None:
    """02_TECHNICAL_DESIGN.md §6 calls for 10-20 short documents."""
    assert 10 <= len(FAQ_CORPUS) <= 20
    assert len(_ALL_DOC_IDS) == len(FAQ_CORPUS)  # all doc_ids unique


@pytest.mark.parametrize("query,expected_top_doc_id", _KNOWN_QUERIES)
def test_hybrid_retrieve_ranks_the_known_correct_document_first(
    query: str, expected_top_doc_id: str
) -> None:
    top_doc_ids = retrieval.hybrid_retrieve(query, top_k=5)
    assert top_doc_ids[0] == expected_top_doc_id
    assert all(doc_id in _ALL_DOC_IDS for doc_id in top_doc_ids)


def test_hybrid_retrieve_respects_top_k() -> None:
    assert len(retrieval.hybrid_retrieve("What is form 1040-X?", top_k=3)) == 3
    assert len(retrieval.hybrid_retrieve("What is form 1040-X?", top_k=1)) == 1


def test_hybrid_retrieve_reuses_cached_corpus_embeddings_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a real bug caught while building this module:
    get_default_embedding_client() originally returned a fresh instance
    every call, which defeated _get_corpus_embeddings()'s identity-keyed
    cache -- the corpus would have been re-embedded via the hosted API
    on every single query in production.
    """
    embed_call_count = 0
    client = retrieval.get_default_embedding_client()
    original_embed = client.embed

    def _counting_embed(text: str):
        nonlocal embed_call_count
        embed_call_count += 1
        return original_embed(text)

    monkeypatch.setattr(client, "embed", _counting_embed)

    retrieval.hybrid_retrieve("What is form 1040-X?", embedding_client=client)
    calls_after_first_query = embed_call_count
    retrieval.hybrid_retrieve("What is the PATH Act?", embedding_client=client)
    calls_after_second_query = embed_call_count

    # Each query embeds only the query itself (+1); the corpus's 18
    # documents must not be re-embedded on the second call.
    assert calls_after_second_query - calls_after_first_query == 1


def test_reciprocal_rank_fusion_favors_documents_ranked_high_in_both_lists() -> None:
    dense_ranking = ["a", "b", "c", "d"]
    sparse_ranking = ["b", "a", "d", "c"]

    fused = retrieval.reciprocal_rank_fusion([dense_ranking, sparse_ranking])

    # "a" and "b" are ranked 1st/2nd in both lists (in some order) --
    # both must outrank "c" and "d", which are ranked 3rd/4th in both.
    assert set(fused[:2]) == {"a", "b"}
    assert set(fused[2:]) == {"c", "d"}


def test_reciprocal_rank_fusion_a_document_missing_from_one_list_still_scores() -> None:
    """A doc_id absent from one ranking (e.g. BM25 gave it a zero
    score and it was excluded) still gets fused in via whichever
    ranking(s) it does appear in -- RRF must not require presence in
    every input list.
    """
    dense_ranking = ["only-in-dense", "shared"]
    sparse_ranking = ["shared"]

    fused = retrieval.reciprocal_rank_fusion([dense_ranking, sparse_ranking])

    assert "only-in-dense" in fused
    assert "shared" in fused
    # "shared" appears in both lists (1st in dense, 1st in sparse) so it
    # must outrank "only-in-dense" (1st in dense, absent from sparse).
    assert fused.index("shared") < fused.index("only-in-dense")


class _FakeReranker:
    """Deterministic, injectable reranker for confidence-floor tests --
    decoupled from whichever real reranker the environment would select.
    """

    def __init__(self, scores: list) -> None:
        self._scores = scores

    def rerank(self, query, candidates) -> list:
        return [
            RankedDocument(document=doc, score=score)
            for doc, score in zip(candidates, self._scores)
        ]


def test_retrieve_and_rerank_confident_when_top_score_meets_floor() -> None:
    outcome = retrieval.retrieve_and_rerank(
        "What is form 1040-X?",
        reranker=_FakeReranker([0.9, 0.4, 0.3, 0.2, 0.1]),
    )
    assert outcome.confident is True
    assert outcome.ranked_documents[0].score == 0.9
    assert outcome.reason is None


def test_retrieve_and_rerank_not_confident_when_top_score_below_floor() -> None:
    outcome = retrieval.retrieve_and_rerank(
        "What is form 1040-X?",
        reranker=_FakeReranker([0.2, 0.1, 0.05, 0.0, 0.0]),
    )
    assert outcome.confident is False
    assert outcome.ranked_documents == []
    assert outcome.reason is not None


def test_retrieve_and_rerank_boundary_score_exactly_at_floor_is_confident() -> None:
    """CONFIDENCE_FLOOR itself should count as confident (>=, not >) --
    an explicit boundary check rather than leaving it implicit.
    """
    outcome = retrieval.retrieve_and_rerank(
        "What is form 1040-X?",
        reranker=_FakeReranker(
            [retrieval.CONFIDENCE_FLOOR, 0.1, 0.05, 0.0, 0.0]
        ),
    )
    assert outcome.confident is True


def test_lexical_fallback_reranker_is_selected_with_no_credentials() -> None:
    assert isinstance(
        retrieval.get_default_reranker(), retrieval._LexicalFallbackReranker
    )


def test_local_hash_embedding_client_is_selected_with_no_credentials() -> None:
    assert isinstance(
        retrieval.get_default_embedding_client(), retrieval._LocalHashEmbeddingClient
    )
