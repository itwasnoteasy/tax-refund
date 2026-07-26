# Tests

Three layers, per `docs/spec/07_TESTING_STRATEGY.md`:

- `test_unit_*.py` — Layer 1: isolated function/class logic, no network, no app instance.
- `test_integration_*.py` — Layer 2: full request round trips and API contract compliance against `docs/spec/03_API_CONTRACT.yaml`.
- Layer 3 (the six acceptance scenarios in `docs/spec/05_ACCEPTANCE_CRITERIA.md`): **not a separate `test_scenario_*.py` file** — see `DECISIONS.md`'s "tests/: Layer 3 scenario coverage lives inside test_integration_refund_status.py" entry for why. Each scenario is a specific, named test in `test_integration_refund_status.py`:
  - Scenario 1 (cache warm) → `test_cache_hit_returns_without_calling_irs_at_all`
  - Scenario 2 (forced cache miss) → `test_cache_miss_calls_irs_exactly_once_and_populates_cache`
  - Scenario 3 (`FAILING` x3 → stale fallback) → `test_circuit_opens_after_three_failures_and_serves_stale_fallback`, `test_circuit_open_short_circuits_without_attempting_irs`
  - Scenario 4 (EITC/CTC → PATH Act) → `test_eitc_ctc_return_gets_path_act_explanation`
  - Scenario 5 (no inferable reason → "still processing") → implicit in the normal, unflagged seed return used above (explanation is asserted indirectly; no dedicated test, since it's the default/no-flag case)
  - Scenario 6 (balance-due / no refund pending) → `test_no_refund_pending_return_has_no_prediction_or_explanation`

Run each layer separately to keep the three-layer report honest (a combined pass count can hide a Layer 3 regression inside an otherwise-green Layer 1/2 result — see `07_TESTING_STRATEGY.md`'s regression policy):

```bash
pytest tests/test_unit_*.py -v          # Layer 1
pytest tests/test_integration_*.py -v   # Layer 2 (includes the Layer 3 scenario tests above)
```
