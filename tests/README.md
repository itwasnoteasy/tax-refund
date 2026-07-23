# Tests

Three layers, per `docs/spec/07_TESTING_STRATEGY.md` — do not blend them:

- `test_unit_*.py` — Layer 1: isolated function/class logic, no network, no app instance.
- `test_integration_*.py` — Layer 2: full request round trips and API contract compliance against `docs/spec/03_API_CONTRACT.yaml`.
- `test_scenario_*.py` — Layer 3: the six acceptance scenarios in `docs/spec/05_ACCEPTANCE_CRITERIA.md`.

No test files yet — the circuit breaker, cache TTL logic, and explanation/prediction stub are all test-first per `CLAUDE.md`'s development-loop rule, so tests for those land before their implementations, not as part of this scaffold.
