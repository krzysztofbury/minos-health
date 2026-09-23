from collections.abc import Mapping
from typing import final

import pytest

from scripts.aws_watcher_operator_simulate import Scenario, evaluate


@final
class _Simulator:
    def __init__(self, decision: str, *, truncated: bool = False) -> None:
        self.decision = decision
        self.truncated = truncated
        self.request: dict[str, object] | None = None

    def simulate_custom_policy(self, **kwargs: object) -> Mapping[str, object]:
        self.request = kwargs
        return {
            "IsTruncated": self.truncated,
            "EvaluationResults": [{"EvalDecision": self.decision}],
        }


def test_simulator_uses_exact_action_resource_and_context() -> None:
    client = _Simulator("allowed")
    scenario = Scenario(
        "watcher_boundary",
        "s3:PutObject",
        "arn:aws:s3:::minos-health-snapshots-218375352108-eu-central-1/blobs/sha256/abc",
        "allowed",
        (("s3:if-none-match", "*"),),
    )

    evaluate(client, {"watcher_boundary": '{"Version":"2012-10-17"}'}, scenario)

    assert client.request == {
        "PolicyInputList": ['{"Version":"2012-10-17"}'],
        "ActionNames": ["s3:PutObject"],
        "ResourceArns": [scenario.resource],
        "MaxItems": 2,
        "ContextEntries": [
            {
                "ContextKeyName": "s3:if-none-match",
                "ContextKeyValues": ["*"],
                "ContextKeyType": "string",
            }
        ],
    }


@pytest.mark.parametrize(
    ("decision", "truncated"),
    [("implicitDeny", False), ("allowed", True)],
)
def test_simulator_rejects_unexpected_or_truncated_results(
    decision: str, truncated: bool
) -> None:
    client = _Simulator(decision, truncated=truncated)
    scenario = Scenario(
        "github_reader_boundary", "s3:PutObject", "arn:aws:s3:::test/x", "allowed"
    )

    with pytest.raises(ValueError):
        evaluate(client, {"github_reader_boundary": "{}"}, scenario)
