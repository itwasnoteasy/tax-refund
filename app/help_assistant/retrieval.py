"""Hybrid dense+sparse retrieval, RRF fusion, cross-encoder rerank (or
LLM-rerank fallback).

Built against docs/spec/02_TECHNICAL_DESIGN.md §6, as committed at
eb360db.

Cold-start decision, made now rather than deferred (# DECISION, see
DECISIONS.md for the full reasoning): this module does NOT load
`cross-encoder/ms-marco-MiniLM-L-6-v2` or any other local
sentence-transformers model. `sentence-transformers` pulls in PyTorch
as a transitive dependency -- hundreds of MB to multiple GB installed --
which risks exceeding Vercel's serverless function size limits outright,
not just adding cold-start latency. That risk doesn't require an
empirical deploy to confirm; it's a well-known, structural property of
the dependency, not something specific to this app's usage of it. This
module goes straight to the LLM-based reranking fallback
02_TECHNICAL_DESIGN.md §6 already describes as the documented
alternative.

Dense retrieval never loads a local embedding model either, per the
same section's cold-start reasoning -- it calls a hosted embedding API.

Both the embedding client and the LLM reranker follow the same
real-backend/local-fallback pattern as kv_store.py: a real
implementation that calls the hosted API when credentials are present,
and a deterministic, dependency-free local fallback otherwise, so
local development and this module's Layer 1 tests need no network
access and no API key. Neither real call could be exercised from this
sandboxed dev environment directly -- but both have since been
confirmed working end-to-end against the live deployed API (real
GEMINI_API_KEY, gemini-2.5-flash / text-embedding-001) by the project
owner on 2026-07-23, including the auth fix this required (API key via
the x-goog-api-key header, not the ?key= query param -- see
DECISIONS.md).
"""
import os
import re
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from rank_bm25 import BM25Okapi

from app.help_assistant.faq_corpus import FAQ_CORPUS, FAQDocument

# DECISION: RRF's k=60 is the standard constant from the original
# Reciprocal Rank Fusion literature (Cormack et al.) -- not tuned for
# this corpus specifically, just the well-established default.
RRF_K = 60

# DECISION: below this score, the pipeline must not proceed to
# synthesis -- see 02_TECHNICAL_DESIGN.md §6's confidence floor
# requirement. 0.5 is a deliberately conservative midpoint given
# neither the real cross-encoder nor a tuned LLM-rerank prompt has been
# calibrated against real usage yet; revisit once it has been.
CONFIDENCE_FLOOR = 0.5

_EMBEDDING_API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
_LOCAL_EMBEDDING_DIMENSIONS = 512

# DECISION: model names are read from env vars, not hardcoded, since
# neither has been verified against the live API from this environment
# (see DECISIONS.md) -- overriding via Vercel env vars lets a wrong
# guess be corrected without a code change/redeploy cycle.
_DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
_DEFAULT_GEMINI_EMBEDDING_MODEL = "text-embedding-004"


def _gemini_model() -> str:
    """The Gemini model used for reranking/synthesis/guardrail calls.

    Override via the GEMINI_MODEL env var.
    """
    return os.environ.get("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)


def _gemini_embedding_model() -> str:
    """The Gemini model used for dense-retrieval embeddings.

    Override via the GEMINI_EMBEDDING_MODEL env var.
    """
    return os.environ.get("GEMINI_EMBEDDING_MODEL", _DEFAULT_GEMINI_EMBEDDING_MODEL)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase, alphanumeric-only tokenization, shared by BM25 and the
    local embedding fallback so both see the same vocabulary.
    """
    return _TOKEN_PATTERN.findall(text.lower())


@dataclass(frozen=True)
class RankedDocument:
    """A single retrieval/rerank result."""

    document: FAQDocument
    score: float


@dataclass(frozen=True)
class RetrievalOutcome:
    """The result of the full retrieve-then-rerank pipeline.

    Attributes:
        confident: False if the top reranked score fell below
            CONFIDENCE_FLOOR -- callers must not proceed to synthesis
            when this is False.
        ranked_documents: The reranked candidates, best first. Empty
            when confident is False.
        reason: Populated only when confident is False.
    """

    confident: bool
    ranked_documents: List[RankedDocument]
    reason: Optional[str] = None


# --- Dense retrieval: hosted embedding API, real backend + local fallback --


class _HostedEmbeddingClient:
    """Real embedding backend -- a hosted API, never a locally-loaded model.

    # DECISION: Google's embedding models via the Generative Language
    # API, called with a plain HTTPS POST (httpx), not a full SDK --
    # Gemini (the same provider) is already the chosen synthesis model
    # in 02_TECHNICAL_DESIGN.md §6, and a heavier SDK is deferred to
    # Phase 4's fuller needs. Confirmed working end-to-end against the
    # live API on 2026-07-23 (model configurable via
    # GEMINI_EMBEDDING_MODEL). See DECISIONS.md.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def embed(self, text: str) -> List[float]:
        """Embed text via the hosted API.

        Args:
            text: The text to embed.

        Returns:
            A dense embedding vector.
        """
        import httpx

        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{_gemini_embedding_model()}:embedContent"
        )
        # DECISION: API key in the x-goog-api-key header, not the
        # ?key= query param -- confirmed live that newer-format ("AQ.")
        # Google API keys are rejected via the query param but accepted
        # via this header. See DECISIONS.md.
        response = httpx.post(
            endpoint,
            headers={"x-goog-api-key": self._api_key},
            json={"content": {"parts": [{"text": text}]}},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()["embedding"]["values"]


class _LocalHashEmbeddingClient:
    """Local fallback: a deterministic hashed bag-of-words vector.

    # FAILURE MODE: this is NOT a locally-loaded embedding model -- no
    # ML model, no weights, no cold-start risk of the kind
    # 02_TECHNICAL_DESIGN.md §6 warns against. It's arithmetic (a
    # feature-hashing trick: count tokens into `zlib.crc32(token) %
    # dimensions` buckets), good enough to rank a small, topically
    # distinct FAQ corpus sensibly for local development and this
    # module's Layer 1 tests, but not a substitute for real semantic
    # embeddings in production. Only selected when no hosted-API
    # credentials are present -- see get_default_embedding_client().
    """

    def embed(self, text: str) -> List[float]:
        vector = [0.0] * _LOCAL_EMBEDDING_DIMENSIONS
        for token in _tokenize(text):
            index = zlib.crc32(token.encode("utf-8")) % _LOCAL_EMBEDDING_DIMENSIONS
            vector[index] += 1.0
        return vector


_default_embedding_client_cache = None
_default_embedding_client_cache_key: Optional[tuple] = None


def get_default_embedding_client():
    """Select an embedding backend based on credential presence.

    # DECISION: memoized (returns the same instance across calls, as
    # long as env vars haven't changed), not constructed fresh each
    # time. _get_corpus_embeddings() caches by embedding_client
    # *identity* -- a fresh instance every call would defeat that
    # cache entirely, forcing the corpus to be re-embedded via the
    # hosted API on every single query, exactly what precomputing it
    # was meant to avoid. Caught by test_hybrid_retrieve_reuses_cached_corpus_embeddings_across_calls
    # before it ever shipped.

    Returns:
        _HostedEmbeddingClient if GEMINI_API_KEY or GOOGLE_API_KEY is
        set; otherwise _LocalHashEmbeddingClient.
    """
    global _default_embedding_client_cache, _default_embedding_client_cache_key

    for env_var in _EMBEDDING_API_KEY_ENV_VARS:
        api_key = os.environ.get(env_var)
        if api_key:
            cache_key = ("hosted", env_var, api_key)
            if _default_embedding_client_cache_key != cache_key:
                _default_embedding_client_cache = _HostedEmbeddingClient(api_key)
                _default_embedding_client_cache_key = cache_key
            return _default_embedding_client_cache

    cache_key = ("local",)
    if _default_embedding_client_cache_key != cache_key:
        _default_embedding_client_cache = _LocalHashEmbeddingClient()
        _default_embedding_client_cache_key = cache_key
    return _default_embedding_client_cache


def reset_default_embedding_client_cache() -> None:
    """Clear the memoized default embedding client. Test-support only."""
    global _default_embedding_client_cache, _default_embedding_client_cache_key
    _default_embedding_client_cache = None
    _default_embedding_client_cache_key = None


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


_corpus_embeddings_cache: Optional[Dict[str, List[float]]] = None
_corpus_embeddings_cache_client: object = None


def _get_corpus_embeddings(embedding_client) -> Dict[str, List[float]]:
    """Corpus embeddings computed once per (process, embedding_client),
    not per query -- re-embedding 18 static documents on every request
    would multiply hosted-API cost/latency for no benefit.
    """
    global _corpus_embeddings_cache, _corpus_embeddings_cache_client
    if (
        _corpus_embeddings_cache is None
        or _corpus_embeddings_cache_client is not embedding_client
    ):
        _corpus_embeddings_cache = {
            doc.doc_id: embedding_client.embed(doc.content) for doc in FAQ_CORPUS
        }
        _corpus_embeddings_cache_client = embedding_client
    return _corpus_embeddings_cache


def reset_corpus_embeddings_cache() -> None:
    """Clear the corpus embeddings cache. Test-support only."""
    global _corpus_embeddings_cache, _corpus_embeddings_cache_client
    _corpus_embeddings_cache = None
    _corpus_embeddings_cache_client = None


def _dense_rank(query: str, embedding_client) -> List[str]:
    """Rank all corpus doc_ids by embedding cosine similarity to query."""
    corpus_embeddings = _get_corpus_embeddings(embedding_client)
    query_embedding = embedding_client.embed(query)
    scored = [
        (doc_id, _cosine_similarity(query_embedding, doc_embedding))
        for doc_id, doc_embedding in corpus_embeddings.items()
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [doc_id for doc_id, _ in scored]


# --- Sparse retrieval: BM25 -------------------------------------------------

_bm25_index_cache: Optional[BM25Okapi] = None
_bm25_doc_ids_cache: Optional[List[str]] = None


def _get_bm25_index() -> Tuple[BM25Okapi, List[str]]:
    global _bm25_index_cache, _bm25_doc_ids_cache
    if _bm25_index_cache is None:
        tokenized_corpus = [
            _tokenize(f"{doc.title} {doc.content}") for doc in FAQ_CORPUS
        ]
        _bm25_index_cache = BM25Okapi(tokenized_corpus)
        _bm25_doc_ids_cache = [doc.doc_id for doc in FAQ_CORPUS]
    return _bm25_index_cache, _bm25_doc_ids_cache


def reset_bm25_index_cache() -> None:
    """Clear the BM25 index cache. Test-support only."""
    global _bm25_index_cache, _bm25_doc_ids_cache
    _bm25_index_cache = None
    _bm25_doc_ids_cache = None


def _sparse_rank(query: str) -> List[str]:
    """Rank all corpus doc_ids by BM25 score against query."""
    index, doc_ids = _get_bm25_index()
    scores = index.get_scores(_tokenize(query))
    ranked = sorted(zip(doc_ids, scores), key=lambda pair: pair[1], reverse=True)
    return [doc_id for doc_id, _ in ranked]


# --- RRF fusion --------------------------------------------------------------


def reciprocal_rank_fusion(rankings: List[List[str]], k: int = RRF_K) -> List[str]:
    """Fuse multiple doc_id rankings via Reciprocal Rank Fusion.

    Args:
        rankings: One or more complete orderings of doc_ids (best
            first), e.g. a dense ranking and a sparse ranking.
        k: RRF's constant -- see RRF_K's module-level comment.

    Returns:
        Fused doc_ids, best first, by descending RRF score.
    """
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)


def hybrid_retrieve(
    query: str, embedding_client=None, top_k: int = 5
) -> List[str]:
    """Retrieve the top_k corpus doc_ids for a query via dense+BM25+RRF.

    Args:
        query: The user's free-text question.
        embedding_client: Defaults to get_default_embedding_client().
        top_k: How many fused candidates to return.

    Returns:
        The top_k doc_ids, best first.
    """
    client = embedding_client or get_default_embedding_client()
    dense_ranking = _dense_rank(query, client)
    sparse_ranking = _sparse_rank(query)
    fused = reciprocal_rank_fusion([dense_ranking, sparse_ranking])
    return fused[:top_k]


# --- Reranking: LLM-based (cross-encoder skipped, see module docstring) ----


class _LLMReranker:
    """Real reranker -- asks Gemini Flash to score each candidate's
    relevance to the query, 0.0-1.0.

    # DECISION: the cross-encoder path (cross-encoder/ms-marco-MiniLM-L-6-v2)
    # was not attempted -- see this module's docstring for why the risk
    # was assessed as disqualifying rather than merely worth measuring.
    # Confirmed working end-to-end against the live API on 2026-07-23.
    # See DECISIONS.md.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def rerank(
        self, query: str, candidates: List[FAQDocument]
    ) -> List[RankedDocument]:
        import json

        import httpx

        candidate_texts = "\n".join(
            f"{i}. {doc.title}: {doc.content}" for i, doc in enumerate(candidates)
        )
        prompt = (
            "Score how relevant each numbered FAQ answer below is to the "
            f"question, from 0.0 (irrelevant) to 1.0 (directly answers it). "
            f'Respond with only a JSON array of numbers, in order.\n\nQuestion: "{query}"\n\n'
            f"{candidate_texts}"
        )
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{_gemini_model()}:generateContent"
        )
        # DECISION: see the matching comment in _HostedEmbeddingClient.embed()
        # -- x-goog-api-key header, not ?key= query param.
        response = httpx.post(
            endpoint,
            headers={"x-goog-api-key": self._api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=10.0,
        )
        response.raise_for_status()
        raw_text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        scores = json.loads(raw_text)
        ranked = sorted(
            zip(candidates, scores), key=lambda pair: pair[1], reverse=True
        )
        return [RankedDocument(document=doc, score=score) for doc, score in ranked]


class _LexicalFallbackReranker:
    """Local, credential-free fallback -- reuses BM25 score against just
    this candidate set, min-max normalized to roughly 0-1 so it's
    comparable to CONFIDENCE_FLOOR the same way the real LLM scores
    would be.

    # FAILURE MODE: this is a fallback of a fallback, for local
    # development and testing only. It has no notion of semantic
    # relevance beyond lexical overlap -- selected automatically only
    # when no Gemini credentials are present, mirroring
    # get_default_embedding_client()'s selection logic.
    """

    def rerank(
        self, query: str, candidates: List[FAQDocument]
    ) -> List[RankedDocument]:
        tokenized_query = _tokenize(query)
        tokenized_candidates = [
            _tokenize(f"{doc.title} {doc.content}") for doc in candidates
        ]
        bm25 = BM25Okapi(tokenized_candidates)
        raw_scores = list(bm25.get_scores(tokenized_query))
        max_score = max(raw_scores) if raw_scores else 0.0
        normalized = [
            (score / max_score if max_score > 0 else 0.0) for score in raw_scores
        ]
        ranked = sorted(
            zip(candidates, normalized), key=lambda pair: pair[1], reverse=True
        )
        return [RankedDocument(document=doc, score=score) for doc, score in ranked]


def get_default_reranker():
    """Select a reranker backend based on credential presence.

    Returns:
        _LLMReranker if GEMINI_API_KEY or GOOGLE_API_KEY is set;
        otherwise _LexicalFallbackReranker.
    """
    for env_var in _EMBEDDING_API_KEY_ENV_VARS:
        api_key = os.environ.get(env_var)
        if api_key:
            return _LLMReranker(api_key)
    return _LexicalFallbackReranker()


def retrieve_and_rerank(
    query: str, top_k: int = 5, reranker=None
) -> RetrievalOutcome:
    """The full pipeline: hybrid retrieve -> rerank -> confidence floor.

    Args:
        query: The user's free-text question.
        top_k: How many fused candidates to rerank.
        reranker: Defaults to get_default_reranker().

    Returns:
        A RetrievalOutcome. If the top reranked score is below
        CONFIDENCE_FLOOR, confident is False and ranked_documents is
        empty -- callers must not proceed to synthesis in that case
        (02_TECHNICAL_DESIGN.md §6).
    """
    candidate_ids = hybrid_retrieve(query, top_k=top_k)
    candidate_docs_by_id = {doc.doc_id: doc for doc in FAQ_CORPUS}
    candidates = [candidate_docs_by_id[doc_id] for doc_id in candidate_ids]

    active_reranker = reranker or get_default_reranker()
    ranked = active_reranker.rerank(query, candidates)

    if not ranked or ranked[0].score < CONFIDENCE_FLOOR:
        # FAILURE MODE: top result too weak to trust -- short-circuit
        # before synthesis, never proceed on a weak-context guess
        # (02_TECHNICAL_DESIGN.md §6).
        return RetrievalOutcome(
            confident=False,
            ranked_documents=[],
            reason="Top retrieved result scored below the confidence floor.",
        )
    return RetrievalOutcome(confident=True, ranked_documents=ranked)
