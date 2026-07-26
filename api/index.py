"""FastAPI application entry point -- Vercel's expected serverless location.

Wires every route in docs/spec/03_API_CONTRACT.yaml, including
/help/ask (the Tax Help Assistant). Built against that contract and
docs/spec/02_TECHNICAL_DESIGN.md, as committed at eb360db.

Route organization: three separate APIRouter objects (refund-status,
notifications, demo-control), not routes defined flat on `app`. The
demo-control router is additionally prefixed and tagged distinctly per
03_API_CONTRACT.yaml's own instruction: "Must be clearly separated from
the real API routes ... so it's obvious this is demo scaffolding, not
part of the contract a real client would call" -- a separate router
object (visibly grouped under its own heading in /docs) satisfies that
structurally, not just by path naming.

Response/request models here are hand-written to mirror
03_API_CONTRACT.yaml's schemas field-for-field, deliberately not
generated from this app's own dataclasses -- so the contract stays the
authority FastAPI's OpenAPI output is checked against
(test_integration_api_contract.py), not the other way around.
"""
from datetime import date, datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import cache_layer, irs_integration, mock_irs, notifications, refund_status, storage
from app.help_assistant import graph as help_assistant_graph

app = FastAPI(
    title="TurboTax Refund Status PoC API",
    version="0.1.0-poc",
)


# --- Request/response models, mirroring 03_API_CONTRACT.yaml exactly ---


class ErrorResponse(BaseModel):
    error: str
    detail: str


class PredictedWindowModel(BaseModel):
    start_date: date
    end_date: date
    confidence_level: float


class RefundStatusResponseModel(BaseModel):
    return_id: str
    tax_year: int
    status_code: Literal["RECEIVED", "APPROVED", "SENT", "NO_REFUND_PENDING"]
    filing_status: str
    expected_refund_amount: Optional[float] = None
    predicted_window: Optional[PredictedWindowModel] = None
    explanation: Optional[str] = None
    stale: bool
    last_checked: datetime


class NotificationOptInRequestModel(BaseModel):
    return_id: str
    channel: Literal["email", "push"]


class NotificationOptInResponseModel(BaseModel):
    return_id: str
    opted_in: bool


class NotificationPreviewResponseModel(BaseModel):
    return_id: str
    rendered_html: str
    note: str


class HelpAskRequestModel(BaseModel):
    question: str


class HelpAskResponseModel(BaseModel):
    question: str
    answered: bool
    answer: Optional[str] = None
    sources: List[str] = []
    confidence: Optional[float] = None
    fallback_message: Optional[str] = None


class SetIRSModeRequestModel(BaseModel):
    mode: Literal[
        "NORMAL",
        "FAILING",
        "FLAPPING",
        "SLOW",
        "TIMEOUT",
        "MALFORMED_RESPONSE",
        "UNKNOWN_STATUS_CODE",
        "RATE_LIMITED",
    ]


class ClearCacheRequestModel(BaseModel):
    return_id: str
    tax_year: int


class CircuitBreakerStatusModel(BaseModel):
    state: Literal["CLOSED", "OPEN", "HALF_OPEN"]


# --- HTTP-layer-specific errors (not domain concepts -- these exist to
# map onto 03_API_CONTRACT.yaml's declared error responses) ------------


class InvalidTaxYearError(Exception):
    """tax_year isn't one of the 3 most recent tax years -- FR-1 in
    01_SPEC.md, surfaced as 03_API_CONTRACT.yaml's 400 response.
    """


def _valid_tax_years() -> set:
    """The 3 most recent tax years, relative to today.

    # DECISION: computed as (this calendar year - 1, -2, -3) -- a filer
    # is checking on a return for a year that's already ended, so
    # "most recent" means the most recently completed tax year and the
    # two before it. Neither 01_SPEC.md nor 03_API_CONTRACT.yaml gives
    # an exact formula; this is my derivation. See DECISIONS.md.
    """
    current_year = date.today().year
    return {current_year - 1, current_year - 2, current_year - 3}


@app.exception_handler(storage.ReturnNotFoundError)
async def _return_not_found_handler(
    request: Request, exc: storage.ReturnNotFoundError
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(error="return_not_found", detail=str(exc)).model_dump(),
    )


@app.exception_handler(refund_status.RefundDataUnavailableError)
async def _refund_data_unavailable_handler(
    request: Request, exc: refund_status.RefundDataUnavailableError
) -> JSONResponse:
    # FAILURE MODE: no fresh IRS data and no cached fallback exists --
    # genuinely nothing to serve. Not declared in 03_API_CONTRACT.yaml's
    # response set (only 200/404/400 are); 503 is this route's own
    # judgment call for a condition the contract doesn't cover, since
    # this reflects the *system* being unable to answer right now, not
    # a client error. See DECISIONS.md.
    return JSONResponse(
        status_code=503,
        content=ErrorResponse(
            error="refund_data_unavailable", detail=str(exc)
        ).model_dump(),
    )


@app.exception_handler(InvalidTaxYearError)
async def _invalid_tax_year_handler(
    request: Request, exc: InvalidTaxYearError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(error="invalid_tax_year", detail=str(exc)).model_dump(),
    )


# --- Refund status -------------------------------------------------------

refund_status_router = APIRouter(tags=["refund-status"])


@refund_status_router.get(
    "/refund-status/{return_id}", response_model=RefundStatusResponseModel
)
def get_refund_status(return_id: str, tax_year: int) -> RefundStatusResponseModel:
    """Cache-first refund status lookup. See 02_TECHNICAL_DESIGN.md §2."""
    if tax_year not in _valid_tax_years():
        raise InvalidTaxYearError(
            f"tax_year {tax_year} is not one of the 3 most recent tax "
            f"years ({sorted(_valid_tax_years())})."
        )
    view = refund_status.get_refund_status_view(return_id, tax_year)
    return _view_to_response_model(view)


def _view_to_response_model(
    view: refund_status.RefundStatusView,
) -> RefundStatusResponseModel:
    predicted_window = None
    if view.predicted_window is not None:
        predicted_window = PredictedWindowModel(
            start_date=view.predicted_window.start_date,
            end_date=view.predicted_window.end_date,
            confidence_level=view.predicted_window.confidence_level,
        )
    return RefundStatusResponseModel(
        return_id=view.return_id,
        tax_year=view.tax_year,
        status_code=view.status_code.value,
        filing_status=view.filing_status,
        expected_refund_amount=view.expected_refund_amount,
        predicted_window=predicted_window,
        explanation=view.explanation,
        stale=view.stale,
        last_checked=view.last_checked,
    )


# --- Notifications ---------------------------------------------------------

notifications_router = APIRouter(tags=["notifications"])


@notifications_router.post(
    "/notifications/opt-in", response_model=NotificationOptInResponseModel
)
def opt_in_to_notifications(
    payload: NotificationOptInRequestModel,
) -> NotificationOptInResponseModel:
    """Opt a return in to status-change notifications. Preview-only
    delivery -- see notifications.py.
    """
    opted_in = notifications.opt_in(payload.return_id, payload.channel)
    return NotificationOptInResponseModel(
        return_id=payload.return_id, opted_in=opted_in
    )


@notifications_router.get(
    "/notifications/preview/{return_id}",
    response_model=NotificationPreviewResponseModel,
)
def preview_notification(return_id: str) -> NotificationPreviewResponseModel:
    """Render the notification email that WOULD be sent. Never sends it."""
    rendered_html = notifications.render_preview(return_id)
    return NotificationPreviewResponseModel(
        return_id=return_id,
        rendered_html=rendered_html,
        note="Preview only -- no live send in this PoC. See DECISIONS.md.",
    )


# --- Tax Help Assistant (FR-7 -- architecturally separate from the
# refund-status core above; the only route in this file backed by an
# LLM, and the only one that can gracefully degrade rather than
# succeed) --------------------------------------------------------------

help_assistant_router = APIRouter(tags=["help-assistant"])


@help_assistant_router.post("/help/ask", response_model=HelpAskResponseModel)
def ask_help_assistant(payload: HelpAskRequestModel) -> HelpAskResponseModel:
    """Ask the Tax Help Assistant an open-ended tax question.

    Never raises for a degraded outcome -- ask_help_assistant() always
    returns a result, answered or gracefully not, per
    02_TECHNICAL_DESIGN.md §6's resilience philosophy for this feature.
    """
    result = help_assistant_graph.ask_help_assistant(payload.question)
    return HelpAskResponseModel(
        question=result.question,
        answered=result.answered,
        answer=result.answer,
        sources=result.sources,
        confidence=result.confidence,
        fallback_message=result.fallback_message,
    )


# --- Demo control (not part of the production API surface) ----------------

demo_router = APIRouter(prefix="/demo", tags=["demo-control"])


@demo_router.post("/set-irs-mode")
def set_irs_mode(payload: SetIRSModeRequestModel) -> dict:
    """[Demo control only] Set the mock IRS's behavior mode.

    Not part of the production API surface -- exists solely to drive
    the demo scenarios live. See mock_irs.py for what each mode
    simulates.
    """
    mock_irs.set_mode(mock_irs.IRSMode(payload.mode))
    return {"mode": payload.mode}


@demo_router.get(
    "/circuit-breaker-status", response_model=CircuitBreakerStatusModel
)
def get_circuit_breaker_status() -> CircuitBreakerStatusModel:
    """[Demo control only] Read the IRS circuit breaker's current state.

    Not in 03_API_CONTRACT.yaml -- added so the demo UI can surface
    CLOSED/OPEN/HALF_OPEN directly rather than only letting a viewer
    infer it from response behavior (stale flags, latency). One global
    breaker, not per-return -- see irs_integration.py.
    """
    return CircuitBreakerStatusModel(state=irs_integration.CircuitBreaker().state.value)


@demo_router.post("/clear-cache")
def clear_cache(payload: ClearCacheRequestModel) -> dict:
    """[Demo control only] Force the next lookup for a return to be a
    genuine cache miss, regardless of remaining TTL.

    Not in 03_API_CONTRACT.yaml -- backs the demo control panel's
    "force cache miss" action (05_ACCEPTANCE_CRITERIA.md's scenario 2).
    """
    cache_layer.clear_cache(payload.return_id, payload.tax_year)
    return {"return_id": payload.return_id, "tax_year": payload.tax_year, "cleared": True}


app.include_router(refund_status_router)
app.include_router(notifications_router)
app.include_router(help_assistant_router)
app.include_router(demo_router)
