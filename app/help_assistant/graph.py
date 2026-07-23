"""LangGraph orchestration: retrieve -> rerank -> synthesize -> guardrail
-> retry-once -> graceful degradation.

Built against docs/spec/02_TECHNICAL_DESIGN.md §6, as committed at
eb360db.

Why LangGraph for something this small: the conditional
retry-on-guardrail-failure logic is a genuine, if small, state machine
(synthesize -> guardrail -> maybe back to synthesize -> maybe degrade),
not a linear pipeline -- proportionate to the task, not framework-
flexing. Reuses the NLI-style contradiction check and confidence-gating
pattern already designed for the broader system
(04_synthesis_guardrails.mermaid) rather than inventing a new guardrail
approach -- adapted here, not reinvented.

Synthesis and the guardrail check both call Gemini Flash directly via
httpx (same lightweight pattern as retrieval.py's embedding/reranker
calls), not a provider SDK -- LangChain (`langchain_core.prompts`) is
used for prompt templating, which is genuine, meaningful use of the
framework without pulling in `langchain-google-genai` before Phase 4's
fuller synthesis needs justify it. See DECISIONS.md.

Neither real backend (Gemini synthesis, Gemini guardrail) has been
exercised against a live API from this environment -- same caveat
already recorded for retrieval.py's embedding/rerank calls and
kv_store.py's real Upstash backend.
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional, TypedDict

import httpx
from langchain_core.prompts import PromptTemplate
from langgraph.graph import END, StateGraph

from app.help_assistant import retrieval
from app.help_assistant.faq_corpus import FAQDocument

_LLM_API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

# DECISION: retry exactly once on a guardrail rejection, per
# 02_TECHNICAL_DESIGN.md §6 -- not a general retry/backoff count, just
# this one specific "try again with the rejection reason fed back in"
# step.
RETRY_LIMIT = 1

# Two distinct fallback messages for two distinct reasons -- the spec
# gives different wording for each, not one generic "can't answer" text:
CONFIDENCE_GATE_FALLBACK_MESSAGE = (
    "I don't have a confident answer to that. For official guidance, see "
    "the IRS's own help resources at irs.gov."
)
GUARDRAIL_OR_ERROR_FALLBACK_MESSAGE = (
    "I'm not able to answer that confidently right now. Please try again "
    "shortly."
)


@dataclass(frozen=True)
class GuardrailResult:
    """The outcome of one NLI-style contradiction check."""

    passed: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class HelpAskResult:
    """Mirrors HelpAskResponse in 03_API_CONTRACT.yaml exactly.

    Attributes:
        question: The original question.
        answered: False if the assistant degraded gracefully.
        answer: Populated only when answered is True.
        sources: FAQ corpus doc_ids the answer was grounded in.
        confidence: The top retrieval/rerank score, when answered.
        fallback_message: Populated only when answered is False.
    """

    question: str
    answered: bool
    answer: Optional[str] = None
    sources: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    fallback_message: Optional[str] = None


# --- Synthesis: Gemini Flash, real backend + extractive local fallback -----


class _GeminiSynthesizer:
    """Real synthesis backend -- Gemini Flash, strictly grounded in the
    retrieved context, with an explicit instruction not to answer
    beyond it. Not exercised against the live API from this
    environment -- see this module's docstring.
    """

    _ENDPOINT = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-flash-latest:generateContent"
    )

    _PROMPT = PromptTemplate.from_template(
        "Answer the user's tax question using ONLY the context below. "
        "If the context doesn't fully answer the question, say so plainly -- "
        "never state anything not directly supported by the context.\n\n"
        "Context:\n{context}\n\nQuestion: {question}{retry_note}\n\nAnswer:"
    )

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def synthesize(
        self,
        question: str,
        context_documents: List[FAQDocument],
        rejection_reason: Optional[str] = None,
    ) -> str:
        """Generate an answer grounded in context_documents.

        Args:
            question: The user's free-text question.
            context_documents: The retrieved, reranked FAQ documents to
                ground the answer in.
            rejection_reason: If this is a retry after a guardrail
                rejection, the reason fed back in to correct it.

        Returns:
            The generated answer text.

        Raises:
            httpx.HTTPError: The request failed, errored, or timed out.
        """
        context_text = "\n\n".join(
            f"[{doc.doc_id}] {doc.title}: {doc.content}"
            for doc in context_documents
        )
        retry_note = (
            f"\n\nYour previous answer was rejected for this reason: "
            f"{rejection_reason}\nRevise your answer to fix this, still "
            f"using only the context above."
            if rejection_reason
            else ""
        )
        prompt = self._PROMPT.format(
            context=context_text, question=question, retry_note=retry_note
        )
        response = httpx.post(
            self._ENDPOINT,
            params={"key": self._api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0][
            "text"
        ].strip()


class _ExtractiveFallbackSynthesizer:
    """Local, credential-free fallback -- returns the top retrieved
    document's content verbatim, with no generation step at all.

    # FAILURE MODE: this is not a substitute for real synthesis in
    # production -- it exists so local development and this module's
    # Layer 2 tests can exercise the full retrieve -> synthesize ->
    # guardrail flow deterministically, with no network access.
    # Because it returns the context verbatim, it can never contradict
    # that context, so the guardrail always passes against it -- this
    # is what makes the "well-covered question returns a grounded
    # answer" test possible without a live LLM. Selected automatically
    # only when no Gemini credentials are present, mirroring
    # retrieval.py's get_default_embedding_client()/get_default_reranker().
    """

    def synthesize(
        self,
        question: str,
        context_documents: List[FAQDocument],
        rejection_reason: Optional[str] = None,
    ) -> str:
        if not context_documents:
            return "I don't have information on that in my FAQ corpus."
        return context_documents[0].content


def get_default_synthesizer():
    """Select a synthesizer based on credential presence.

    Returns:
        _GeminiSynthesizer if GEMINI_API_KEY or GOOGLE_API_KEY is set;
        otherwise _ExtractiveFallbackSynthesizer.
    """
    for env_var in _LLM_API_KEY_ENV_VARS:
        api_key = os.environ.get(env_var)
        if api_key:
            return _GeminiSynthesizer(api_key)
    return _ExtractiveFallbackSynthesizer()


# --- Guardrail: NLI-style contradiction check, real backend + lexical fallback --


class _GeminiGuardrail:
    """Real guardrail backend -- asks Gemini itself to judge whether the
    generated answer contradicts, or claims something beyond, the
    retrieved context.

    # DECISION: an LLM-prompted check, not a locally-loaded NLI
    # classifier model -- the same cold-start reasoning that ruled out
    # a local cross-encoder in retrieval.py applies here too (a real
    # NLI model is a real ML dependency with the same deployability
    # risk). Not exercised against the live API from this environment.
    # See DECISIONS.md.
    """

    _ENDPOINT = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-flash-latest:generateContent"
    )

    _PROMPT = PromptTemplate.from_template(
        "You are checking for contradiction, not general quality. "
        "Premise (retrieved context): {context}\n\n"
        "Hypothesis (generated answer): {answer}\n\n"
        "Does the hypothesis contradict the premise, or state anything not "
        "supported by it? Respond with only one word: CONSISTENT or "
        "CONTRADICTION."
    )

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def check(self, context: str, answer: str) -> GuardrailResult:
        """Run the contradiction check.

        Args:
            context: The retrieved context (premise).
            answer: The generated answer (hypothesis).

        Returns:
            A GuardrailResult.

        Raises:
            httpx.HTTPError: The request failed, errored, or timed out.
        """
        prompt = self._PROMPT.format(context=context, answer=answer)
        response = httpx.post(
            self._ENDPOINT,
            params={"key": self._api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=10.0,
        )
        response.raise_for_status()
        raw_text = response.json()["candidates"][0]["content"]["parts"][0][
            "text"
        ].upper()
        passed = "CONTRADICTION" not in raw_text
        return GuardrailResult(
            passed=passed,
            reason=(
                None
                if passed
                else "The generated answer contradicted, or went beyond, "
                "the retrieved context."
            ),
        )


_LEXICAL_OVERLAP_THRESHOLD = 0.5


class _LexicalOverlapGuardrail:
    """Local, credential-free fallback -- checks that most of the
    answer's words actually appear in the retrieved context, as a crude
    stand-in for a real entailment check.

    # FAILURE MODE: not a real contradiction check -- pure word overlap,
    # so a fluent but unsupported claim built entirely from
    # context-adjacent vocabulary could slip through. Local dev/test
    # only; selected automatically only when no Gemini credentials are
    # present.
    """

    def check(self, context: str, answer: str) -> GuardrailResult:
        context_tokens = set(retrieval._tokenize(context))
        answer_tokens = set(retrieval._tokenize(answer))
        if not answer_tokens:
            return GuardrailResult(passed=False, reason="Empty answer.")
        overlap_ratio = len(answer_tokens & context_tokens) / len(answer_tokens)
        passed = overlap_ratio >= _LEXICAL_OVERLAP_THRESHOLD
        return GuardrailResult(
            passed=passed,
            reason=(
                None
                if passed
                else (
                    f"Only {overlap_ratio:.0%} of the answer's words appear "
                    "in the retrieved context."
                )
            ),
        )


def get_default_guardrail():
    """Select a guardrail based on credential presence.

    Returns:
        _GeminiGuardrail if GEMINI_API_KEY or GOOGLE_API_KEY is set;
        otherwise _LexicalOverlapGuardrail.
    """
    for env_var in _LLM_API_KEY_ENV_VARS:
        api_key = os.environ.get(env_var)
        if api_key:
            return _GeminiGuardrail(api_key)
    return _LexicalOverlapGuardrail()


# --- Graph state and construction -------------------------------------------


class _State(TypedDict):
    question: str
    retrieval_outcome: Optional[retrieval.RetrievalOutcome]
    answer: Optional[str]
    synthesis_failed: bool
    guardrail_passed: Optional[bool]
    guardrail_reason: Optional[str]
    retry_count: int
    degrade_reason: Optional[str]
    result: Optional[HelpAskResult]


def _build_graph(synthesizer, guardrail, reranker):
    """Construct the compiled LangGraph for one request.

    A fresh graph is built per call (cheap -- no I/O happens until a
    node actually runs) so synthesizer/guardrail/reranker can be
    injected per call, which is what makes this deterministically
    testable without live credentials -- see
    test_integration_help_assistant.py.
    """

    def retrieve_node(state: _State) -> dict:
        outcome = retrieval.retrieve_and_rerank(state["question"], reranker=reranker)
        return {"retrieval_outcome": outcome}

    def route_after_retrieve(state: _State) -> str:
        if state["retrieval_outcome"].confident:
            return "synthesize"
        return "degrade_low_confidence"

    def synthesize_node(state: _State) -> dict:
        context_documents = [
            ranked.document for ranked in state["retrieval_outcome"].ranked_documents
        ]
        try:
            answer = synthesizer.synthesize(
                state["question"],
                context_documents,
                rejection_reason=state.get("guardrail_reason"),
            )
        except httpx.HTTPError:
            # FAILURE MODE: the LLM call itself errored or timed out --
            # per 02_TECHNICAL_DESIGN.md §6, this degrades directly,
            # with no retry (retries are specifically for a guardrail
            # rejection of a *received* answer, not a connectivity
            # failure that produced no answer at all).
            return {"answer": None, "synthesis_failed": True}
        return {"answer": answer, "synthesis_failed": False}

    def route_after_synthesize(state: _State) -> str:
        if state["synthesis_failed"]:
            return "degrade_guardrail_or_error"
        return "guardrail"

    def guardrail_node(state: _State) -> dict:
        context_text = "\n\n".join(
            ranked.document.content
            for ranked in state["retrieval_outcome"].ranked_documents
        )
        result = guardrail.check(context_text, state["answer"])
        return {"guardrail_passed": result.passed, "guardrail_reason": result.reason}

    def route_after_guardrail(state: _State) -> str:
        if state["guardrail_passed"]:
            return "success"
        if state["retry_count"] >= RETRY_LIMIT:
            # FAILURE MODE: the retry also failed the guardrail -- never
            # show a flagged response, degrade instead (CLAUDE.md,
            # 02_TECHNICAL_DESIGN.md §6).
            return "degrade_guardrail_or_error"
        return "retry"

    def increment_retry_node(state: _State) -> dict:
        return {"retry_count": state["retry_count"] + 1}

    def success_node(state: _State) -> dict:
        outcome = state["retrieval_outcome"]
        return {
            "result": HelpAskResult(
                question=state["question"],
                answered=True,
                answer=state["answer"],
                sources=[ranked.document.doc_id for ranked in outcome.ranked_documents],
                confidence=outcome.ranked_documents[0].score,
            )
        }

    def degrade_low_confidence_node(state: _State) -> dict:
        return {
            "result": HelpAskResult(
                question=state["question"],
                answered=False,
                fallback_message=CONFIDENCE_GATE_FALLBACK_MESSAGE,
            )
        }

    def degrade_guardrail_or_error_node(state: _State) -> dict:
        return {
            "result": HelpAskResult(
                question=state["question"],
                answered=False,
                fallback_message=GUARDRAIL_OR_ERROR_FALLBACK_MESSAGE,
            )
        }

    graph = StateGraph(_State)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("guardrail", guardrail_node)
    graph.add_node("increment_retry", increment_retry_node)
    graph.add_node("success", success_node)
    graph.add_node("degrade_low_confidence", degrade_low_confidence_node)
    graph.add_node("degrade_guardrail_or_error", degrade_guardrail_or_error_node)

    graph.set_entry_point("retrieve")
    graph.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {"synthesize": "synthesize", "degrade_low_confidence": "degrade_low_confidence"},
    )
    graph.add_conditional_edges(
        "synthesize",
        route_after_synthesize,
        {
            "guardrail": "guardrail",
            "degrade_guardrail_or_error": "degrade_guardrail_or_error",
        },
    )
    graph.add_conditional_edges(
        "guardrail",
        route_after_guardrail,
        {
            "success": "success",
            "retry": "increment_retry",
            "degrade_guardrail_or_error": "degrade_guardrail_or_error",
        },
    )
    graph.add_edge("increment_retry", "synthesize")
    graph.add_edge("success", END)
    graph.add_edge("degrade_low_confidence", END)
    graph.add_edge("degrade_guardrail_or_error", END)

    return graph.compile()


def ask_help_assistant(
    question: str, synthesizer=None, guardrail=None, reranker=None
) -> HelpAskResult:
    """Run the full retrieve -> synthesize -> guardrail -> retry ->
    degrade pipeline for one question.

    Args:
        question: The user's free-text tax question.
        synthesizer: Defaults to get_default_synthesizer(). Injectable
            for deterministic testing.
        guardrail: Defaults to get_default_guardrail(). Injectable for
            deterministic testing.
        reranker: Passed through to retrieval.retrieve_and_rerank();
            defaults to retrieval.get_default_reranker().

    Returns:
        A HelpAskResult -- always, even on failure. This function never
        raises for a degraded outcome; that's the whole point of the
        graceful-degradation path.
    """
    compiled_graph = _build_graph(
        synthesizer or get_default_synthesizer(),
        guardrail or get_default_guardrail(),
        reranker,
    )
    final_state = compiled_graph.invoke(
        {
            "question": question,
            "retrieval_outcome": None,
            "answer": None,
            "synthesis_failed": False,
            "guardrail_passed": None,
            "guardrail_reason": None,
            "retry_count": 0,
            "degrade_reason": None,
            "result": None,
        }
    )
    return final_state["result"]
