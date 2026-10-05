import pytest

from backend.graph.manager import GraphManager

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "identifier",
    ["Character", "TestCustomNode", "node_1", "TEST_EDGE_TYPE", "_", "A1_b2"],
)
def test_accepts_identifier_safe_names(identifier: str) -> None:
    assert GraphManager._validate_identifier(None, identifier) == identifier


@pytest.mark.parametrize(
    "identifier",
    [
        "Character) DETACH DELETE n",
        "Character;DROP",
        "a-b",
        "a b",
        "a.b",
        "a`b`",
        "",
        "Ünicode",
    ],
)
def test_rejects_identifiers_that_are_not_alphanumeric_or_underscore(identifier: str) -> None:
    with pytest.raises(ValueError):
        GraphManager._validate_identifier(None, identifier)
