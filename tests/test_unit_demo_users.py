"""Layer 1 -- demo_users.py's active-user get/set and filing_description
derivation, in isolation. No network, no app instance -- the active-user
KV key goes through the local in-memory fallback here, not real KV. See
docs/spec/07_TESTING_STRATEGY.md.
"""
import pytest

from app import demo_users, kv_store, storage

_KV_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch):
    for name in _KV_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    storage.reset_seed_data()
    kv_store.reset_local_backend()
    yield
    kv_store.reset_local_backend()


def test_get_active_user_id_defaults_when_never_set() -> None:
    assert demo_users.get_active_user_id() == demo_users.DEFAULT_USER_ID


def test_set_then_get_active_user_id_round_trips() -> None:
    demo_users.set_active_user_id("user3")
    assert demo_users.get_active_user_id() == "user3"


def test_set_active_user_id_rejects_unknown_user() -> None:
    with pytest.raises(demo_users.UnknownDemoUserError):
        demo_users.set_active_user_id("not-a-real-user")


def test_list_demo_users_covers_every_seed_return_exactly_once() -> None:
    users = demo_users.list_demo_users()
    return_ids = {u.return_id for u in users}
    assert len(users) == len(return_ids) == 5


def test_filing_description_efile_with_direct_deposit() -> None:
    tax_return = storage.get_tax_return("RET-2025-00001", 2025)
    assert demo_users.filing_description(tax_return) == "e-file with direct deposit"


def test_filing_description_efile_without_bank_account() -> None:
    tax_return = storage.get_tax_return("RET-2025-00003", 2025)
    assert demo_users.filing_description(tax_return) == "e-file with a paper check"


def test_filing_description_paper_filed() -> None:
    tax_return = storage.get_tax_return("RET-2025-00004", 2025)
    assert demo_users.filing_description(tax_return) == "paper-filed return"
