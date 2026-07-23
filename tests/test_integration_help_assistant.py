"""Layer 2 -- the full Tax Help Assistant pipeline: retrieval.py wired
together with graph.py's LangGraph orchestration for real (dependency
injection is used only where the task specifically calls for a
*simulated* condition -- an out-of-corpus confidence gate and a
simulated LLM failure -- not to bypass the graph itself). See
docs/spec/07_TESTING_STRATEGY.md.
"""
import httpx
import pytest

from app.help_assistant import graph, retrieval
from app.help_assistant.retrieval import RankedDocument

_LLM_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch: pytest.MonkeyPatch):
    for name in _LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    retrieval.reset_corpus_embeddings_cache()
    retrieval.reset_bm25_index_cache()
    retrieval.reset_default_embedding_client_cache()
    yield
    retrieval.reset_corpus_embeddings_cache()
    retrieval.reset_bm25_index_cache()
    retrieval.reset_default_embedding_client_cache()


class _AlwaysLowConfidenceReranker:
    """Simulates an out-of-corpus question: every candidate scores below
    CONFIDENCE_FLOOR regardless of retrieval, so the confidence gate
    trips deterministically without depending on finding an actually
    ill-covered real-world question.
    """

    def rerank(self, query, candidates):
        return [
            RankedDocument(document=doc, score=0.05) for doc in candidates
        ]


class _AlwaysFailingReranker:
    """Simulates the retrieval step's own hosted-API call (embedding or
    rerank) erroring or timing out -- a real gap found and fixed after
    the app was actually deployed with live credentials: retrieve_node
    originally had no error handling at all for this.
    """

    def rerank(self, query, candidates):
        raise httpx.TimeoutException("simulated retrieval timeout")


class _AlwaysFailingGuardrail:
    """Simulates the guardrail's own hosted-API call erroring or timing
    out, distinct from it running and rejecting an answer.
    """

    def check(self, context, answer):
        raise httpx.TimeoutException("simulated guardrail timeout")


class _AlwaysFailingSynthesizer:
    """Simulates the LLM call itself erroring or timing out."""

    def synthesize(self, question, context_documents, rejection_reason=None):
        raise httpx.TimeoutException("simulated LLM timeout")


def test_well_covered_question_returns_grounded_answer_with_sources() -> None:
    """Runs the real pipeline end to end (local fallback backends, no
    credentials in this environment) -- confirms the happy path
    produces an answer, sources, and a confidence score, not just that
    it doesn't crash.
    """
    result = graph.ask_help_assistant("What is form 1040-X used for?")

    assert result.answered is True
    assert result.answer is not None
    assert "1040-X" in result.answer or "amend" in result.answer.lower()
    assert "faq-1040x" in result.sources
    assert result.confidence is not None
    assert result.fallback_message is None


def test_out_of_corpus_question_triggers_confidence_gate() -> None:
    """The confidence floor, not the guardrail, is what's being tested
    here -- an injected reranker simulates "nothing in the corpus is a
    good match," which is the scenario 02_TECHNICAL_DESIGN.md §6's
    confidence floor exists to catch before ever reaching synthesis.
    """
    result = graph.ask_help_assistant(
        "Some question the FAQ corpus has no good answer for",
        reranker=_AlwaysLowConfidenceReranker(),
    )

    assert result.answered is False
    assert result.answer is None
    assert result.sources == []
    assert result.fallback_message == graph.CONFIDENCE_GATE_FALLBACK_MESSAGE


def test_simulated_llm_failure_degrades_gracefully_never_raises() -> None:
    """The LLM call itself erroring/timing out must degrade to a calm
    fallback message -- never propagate a raw exception through this
    function, per CLAUDE.md's resilience philosophy for this feature.
    """
    result = graph.ask_help_assistant(
        "What is form 1040-X used for?",
        synthesizer=_AlwaysFailingSynthesizer(),
    )

    assert result.answered is False
    assert result.answer is None
    assert result.fallback_message == graph.GUARDRAIL_OR_ERROR_FALLBACK_MESSAGE


def test_simulated_retrieval_failure_degrades_gracefully_never_raises() -> None:
    """Regression test for a real gap found after deployment: the
    retrieval step's own hosted-API call (embedding or rerank) failing
    must degrade the same way a synthesis failure does, not propagate
    as an unhandled exception.
    """
    result = graph.ask_help_assistant(
        "What is form 1040-X used for?",
        reranker=_AlwaysFailingReranker(),
    )

    assert result.answered is False
    assert result.answer is None
    assert result.fallback_message == graph.GUARDRAIL_OR_ERROR_FALLBACK_MESSAGE


def test_simulated_guardrail_call_failure_degrades_gracefully_never_raises() -> None:
    """The guardrail's own call failing (distinct from it running and
    rejecting an answer) must also degrade gracefully -- an
    unverifiable answer is treated the same as a rejected one, not
    assumed safe to show.
    """
    result = graph.ask_help_assistant(
        "What is form 1040-X used for?",
        guardrail=_AlwaysFailingGuardrail(),
    )

    assert result.answered is False
    assert result.answer is None
    assert result.fallback_message == graph.GUARDRAIL_OR_ERROR_FALLBACK_MESSAGE


def test_guardrail_rejection_triggers_one_retry_then_succeeds() -> None:
    """05_ACCEPTANCE_CRITERIA.md: the guardrail must be demonstrable as
    actually able to block a response, not just described. Here it
    blocks once, forcing a retry with the rejection reason fed back,
    then passes -- confirming the retry path (not just the terminal
    degrade path) genuinely runs.
    """

    class _SynthesizerRecordingRejectionReasons:
        def __init__(self) -> None:
            self.rejection_reasons_seen = []

        def synthesize(self, question, context_documents, rejection_reason=None):
            self.rejection_reasons_seen.append(rejection_reason)
            return f"attempt {len(self.rejection_reasons_seen)}"

    class _GuardrailFailsOnceThenPasses:
        def __init__(self) -> None:
            self.call_count = 0

        def check(self, context, answer):
            self.call_count += 1
            if self.call_count == 1:
                return graph.GuardrailResult(
                    passed=False, reason="contradicts retrieved context"
                )
            return graph.GuardrailResult(passed=True)

    synthesizer = _SynthesizerRecordingRejectionReasons()
    guardrail = _GuardrailFailsOnceThenPasses()

    result = graph.ask_help_assistant(
        "What is form 1040-X used for?",
        synthesizer=synthesizer,
        guardrail=guardrail,
    )

    assert result.answered is True
    assert result.answer == "attempt 2"
    assert synthesizer.rejection_reasons_seen == [
        None,
        "contradicts retrieved context",
    ]
    assert guardrail.call_count == 2


def test_guardrail_rejection_twice_degrades_never_shows_flagged_response() -> None:
    """02_TECHNICAL_DESIGN.md §6: exactly one retry -- if the guardrail
    fails again, degrade, never show the flagged answer.
    """

    class _AlwaysRejectingGuardrail:
        def check(self, context, answer):
            return graph.GuardrailResult(passed=False, reason="always rejects")

    class _SynthesizerCountingCalls:
        def __init__(self) -> None:
            self.call_count = 0

        def synthesize(self, question, context_documents, rejection_reason=None):
            self.call_count += 1
            return "an answer that will always be rejected"

    synthesizer = _SynthesizerCountingCalls()

    result = graph.ask_help_assistant(
        "What is form 1040-X used for?",
        synthesizer=synthesizer,
        guardrail=_AlwaysRejectingGuardrail(),
    )

    assert result.answered is False
    assert result.answer is None  # the rejected answer is never shown
    assert result.fallback_message == graph.GUARDRAIL_OR_ERROR_FALLBACK_MESSAGE
    assert synthesizer.call_count == 2  # 1 initial attempt + 1 retry, no more
