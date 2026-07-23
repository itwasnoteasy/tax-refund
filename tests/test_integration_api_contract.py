"""Layer 2 -- contract compliance: every live response this API actually
returns is validated against the schemas in docs/spec/03_API_CONTRACT.yaml
itself, not against this app's own Pydantic models (which could quietly
drift from the contract without anyone noticing). Per
07_TESTING_STRATEGY.md: "a schema-validation test that loads the OpenAPI
spec and checks live responses against it ... is the single
highest-value test in this layer."

Also confirms /docs and /openapi.json boot correctly under this app's
actual routing -- see this task's chat reply for why that's the
strongest check available without a live Vercel deployment.
"""
import pytest
from fastapi.testclient import TestClient

from api.index import app
from app import kv_store, mock_irs, storage
from app.help_assistant import retrieval
from tests._contract import assert_matches_schema, load_contract

_KV_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)
_LLM_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch):
    for name in _KV_ENV_VARS + _LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    storage.reset_seed_data()
    kv_store.reset_local_backend()
    mock_irs.set_mode(mock_irs.IRSMode.NORMAL)
    retrieval.reset_corpus_embeddings_cache()
    retrieval.reset_bm25_index_cache()
    retrieval.reset_default_embedding_client_cache()
    yield
    kv_store.reset_local_backend()


@pytest.fixture(scope="module")
def contract():
    return load_contract()


@pytest.fixture
def client():
    return TestClient(app)


def test_refund_status_200_matches_contract(client, contract) -> None:
    response = client.get("/refund-status/RET-2025-00001", params={"tax_year": 2025})
    assert response.status_code == 200
    assert_matches_schema(
        response.json(), contract, "/refund-status/{return_id}", "get", "200"
    )


def test_refund_status_no_refund_pending_matches_contract(client, contract) -> None:
    """Acceptance-criteria row 6's response shape -- nullable fields
    (predicted_window, explanation, expected_refund_amount) genuinely
    null here, exercising the nullable-conversion logic in
    tests/_contract.py, not just the happy path above.
    """
    response = client.get("/refund-status/RET-2025-00003", params={"tax_year": 2025})
    assert response.status_code == 200
    assert response.json()["predicted_window"] is None
    assert_matches_schema(
        response.json(), contract, "/refund-status/{return_id}", "get", "200"
    )


def test_refund_status_404_matches_contract(client, contract) -> None:
    response = client.get("/refund-status/RET-9999-99999", params={"tax_year": 2025})
    assert response.status_code == 404
    assert_matches_schema(
        response.json(), contract, "/refund-status/{return_id}", "get", "404"
    )


def test_refund_status_400_matches_contract(client, contract) -> None:
    response = client.get("/refund-status/RET-2025-00001", params={"tax_year": 1999})
    assert response.status_code == 400
    assert_matches_schema(
        response.json(), contract, "/refund-status/{return_id}", "get", "400"
    )


def test_help_ask_200_matches_contract(client, contract) -> None:
    """Uses the local, credential-free fallback (no GEMINI_API_KEY in
    this environment) -- still a real end-to-end run of the LangGraph
    pipeline, just against the deterministic fallback backends.
    """
    response = client.post(
        "/help/ask", json={"question": "What is form 1040-X used for?"}
    )
    assert response.status_code == 200
    assert response.json()["answered"] is True
    assert_matches_schema(response.json(), contract, "/help/ask", "post", "200")


def test_notifications_opt_in_200_matches_contract(client, contract) -> None:
    response = client.post(
        "/notifications/opt-in",
        json={"return_id": "RET-2025-00001", "channel": "email"},
    )
    assert response.status_code == 200
    assert_matches_schema(
        response.json(), contract, "/notifications/opt-in", "post", "200"
    )


def test_notifications_preview_200_matches_contract(client, contract) -> None:
    response = client.get("/notifications/preview/RET-2025-00001")
    assert response.status_code == 200
    assert_matches_schema(
        response.json(),
        contract,
        "/notifications/preview/{return_id}",
        "get",
        "200",
    )


def test_demo_set_irs_mode_matches_contracts_declared_status_only(
    client, contract
) -> None:
    """03_API_CONTRACT.yaml declares no response schema for this
    endpoint (just a 200 description, no `content` key) -- confirmed
    directly here rather than assumed, so this test would fail loudly
    if the contract ever gained a schema for it that this app then
    silently didn't match.
    """
    response = client.post("/demo/set-irs-mode", json={"mode": "SLOW"})
    assert response.status_code == 200

    declared_responses = contract["paths"]["/demo/set-irs-mode"]["post"]["responses"]
    assert "200" in declared_responses
    assert "content" not in declared_responses["200"]


def test_docs_and_openapi_json_are_reachable(client) -> None:
    """The closest local proxy available for "does /docs work" without
    an actual Vercel deployment: runs the exact same ASGI app object
    Vercel's Python runtime would invoke.
    """
    docs_response = client.get("/docs")
    assert docs_response.status_code == 200
    assert "text/html" in docs_response.headers["content-type"]
    assert "swagger-ui" in docs_response.text.lower()

    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 200
    openapi_schema = openapi_response.json()
    for path in (
        "/refund-status/{return_id}",
        "/notifications/opt-in",
        "/notifications/preview/{return_id}",
        "/demo/set-irs-mode",
        "/help/ask",
    ):
        assert path in openapi_schema["paths"]
