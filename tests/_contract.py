"""OpenAPI contract-validation helper, shared by
test_integration_api_contract.py. Not a test module itself (no
test_/_test filename match, so pytest won't collect it).

Converts docs/spec/03_API_CONTRACT.yaml's OpenAPI-specific `nullable:
true` into plain JSON Schema's `type: [X, "null"]` once, up front --
jsonschema has no native concept of `nullable`, so left unconverted,
every nullable field the contract explicitly allows (e.g.
expected_refund_amount, explanation) would incorrectly reject an actual
`null` value.
"""
import warnings
from pathlib import Path
from typing import Any, Dict

import yaml

_SPEC_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "spec" / "03_API_CONTRACT.yaml"
)


def load_contract() -> Dict[str, Any]:
    """Load and OpenAPI-to-JSON-Schema-normalize the API contract.

    Returns:
        The full contract document, with every `nullable: true` node
        converted to a JSON-Schema-compatible `type` list.
    """
    with _SPEC_PATH.open() as f:
        raw_spec = yaml.safe_load(f)
    return _openapi_to_jsonschema(raw_spec)


def assert_matches_schema(
    instance: Any, spec: Dict[str, Any], path: str, method: str, status_code: str
) -> None:
    """Assert a live response body matches the contract's declared schema.

    Args:
        instance: The parsed JSON response body to validate.
        spec: The full, normalized contract document, from load_contract().
        path: The OpenAPI path key (e.g. "/refund-status/{return_id}").
        method: The lowercase HTTP method (e.g. "get").
        status_code: The response status code as a string (e.g. "200").

    Raises:
        jsonschema.exceptions.ValidationError: The instance doesn't
            match the contract's schema for this path/method/status.
        KeyError: The contract declares no schema at all for this
            path/method/status (some responses, like
            POST /demo/set-irs-mode's 200, have no `content` key --
            callers should check for that separately, not call this).
    """
    schema = spec["paths"][path][method]["responses"][status_code]["content"][
        "application/json"
    ]["schema"]
    with warnings.catch_warnings():
        # RefResolver (import included) is deprecated in favor of the
        # `referencing` library, but remains fully functional -- this is
        # test-only tooling, not production code, so the simpler API is
        # the pragmatic choice. Import deferred to here, inside the
        # suppression, since the deprecation warning fires at import
        # time, not just on use.
        warnings.simplefilter("ignore", DeprecationWarning)
        from jsonschema import Draft7Validator, RefResolver

        resolver = RefResolver(base_uri="", referrer=spec)
        validator = Draft7Validator(schema, resolver=resolver)
        validator.validate(instance)


def _openapi_to_jsonschema(node: Any) -> Any:
    if isinstance(node, dict):
        converted = {
            key: _openapi_to_jsonschema(value)
            for key, value in node.items()
            if key != "nullable"
        }
        if node.get("nullable") is True and "type" in converted:
            existing_type = converted["type"]
            type_list = (
                existing_type if isinstance(existing_type, list) else [existing_type]
            )
            if "null" not in type_list:
                type_list = type_list + ["null"]
            converted["type"] = type_list
        return converted
    if isinstance(node, list):
        return [_openapi_to_jsonschema(item) for item in node]
    return node
