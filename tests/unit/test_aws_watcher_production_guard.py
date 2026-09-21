from __future__ import annotations

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from scripts.aws_watcher_production_guard import (
    ProductionGuardError,
    validate_sns_subscription,
)

TOPIC_ARN = "arn:aws:sns:eu-central-1:123456789012:minos-health-watcher-budget-alerts"
SUBSCRIPTION_ARN = f"{TOPIC_ARN}:11111111-2222-3333-4444-555555555555"
SUBSCRIBER_EMAIL = "operator@example.com"
ROOT = Path(__file__).parents[2]


def _response() -> dict[str, object]:
    return {
        "Subscriptions": [
            {
                "Endpoint": SUBSCRIBER_EMAIL,
                "Owner": "123456789012",
                "Protocol": "email",
                "SubscriptionArn": SUBSCRIPTION_ARN,
                "TopicArn": TOPIC_ARN,
            }
        ]
    }


def _subscriptions(document: dict[str, object]) -> list[dict[str, object]]:
    values = document["Subscriptions"]
    assert isinstance(values, list)
    return cast(list[dict[str, object]], values)


def test_exact_confirmed_sns_subscription_is_accepted() -> None:
    validate_sns_subscription(
        _response(),
        topic_arn=TOPIC_ARN,
        subscriber_email=SUBSCRIBER_EMAIL,
        expected_subscription_arn=SUBSCRIPTION_ARN,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "multiple",
        "paginated",
        "topic",
        "endpoint",
        "protocol",
        "pending",
        "unexpected-arn",
    ],
)
def test_incomplete_or_ambiguous_sns_subscription_is_rejected(mutation: str) -> None:
    response = deepcopy(_response())
    subscriptions = _subscriptions(response)
    subscription = subscriptions[0]
    expected_arn = SUBSCRIPTION_ARN

    if mutation == "missing":
        subscriptions.clear()
    elif mutation == "multiple":
        subscriptions.append(deepcopy(subscription))
    elif mutation == "paginated":
        response["NextToken"] = "more-results"
    elif mutation == "topic":
        subscription["TopicArn"] = f"{TOPIC_ARN}-other"
    elif mutation == "endpoint":
        subscription["Endpoint"] = "other@example.com"
    elif mutation == "protocol":
        subscription["Protocol"] = "https"
    elif mutation == "pending":
        subscription["SubscriptionArn"] = "PendingConfirmation"
        expected_arn = "PendingConfirmation"
    else:
        subscription["SubscriptionArn"] = "unexpected"
        expected_arn = "unexpected"

    with pytest.raises(ProductionGuardError):
        validate_sns_subscription(
            response,
            topic_arn=TOPIC_ARN,
            subscriber_email=SUBSCRIBER_EMAIL,
            expected_subscription_arn=expected_arn,
        )


def test_shell_helper_uses_bounded_exact_lookup_and_rejects_pending(
    tmp_path: Path,
) -> None:
    response_path = tmp_path / "response.json"
    arguments_path = tmp_path / "aws-arguments.txt"
    artifacts_path = tmp_path / "artifacts"
    artifacts_path.mkdir()
    environment = {
        **os.environ,
        "ARGS": str(arguments_path),
        "ARTIFACTS": str(artifacts_path),
        "EXPECTED_ARN": SUBSCRIPTION_ARN,
        "RESPONSE": str(response_path),
    }
    command = r"""
source scripts/aws-watcher-common.sh
WATCHER_TIMEOUT_SECONDS=30
watcher_terraform() {
  case "$3" in
    budget_alert_topic_arn) printf '%s' 'arn:aws:sns:eu-central-1:123456789012:minos-health-watcher-budget-alerts' ;;
    budget_alert_subscriber_email) printf '%s' 'operator@example.com' ;;
    budget_alert_subscription_arn) printf '%s' "$EXPECTED_ARN" ;;
    *) return 1 ;;
  esac
}
timeout() {
  [[ "$1" == "30" ]] || return 90
  shift
  "$@"
}
aws() {
  printf '%s\n' "$@" >"$ARGS"
  python3 -c 'import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())' "$RESPONSE"
}
watcher_verify_budget_subscription minos-production eu-central-1 "$ARTIFACTS"
"""

    _ = response_path.write_text(json.dumps(_response()))
    accepted = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert arguments_path.read_text().splitlines() == [
        "--profile",
        "minos-production",
        "--region",
        "eu-central-1",
        "sns",
        "list-subscriptions-by-topic",
        "--topic-arn",
        TOPIC_ARN,
        "--max-items",
        "2",
        "--no-cli-pager",
        "--output",
        "json",
    ]
    assert not (artifacts_path / "budget-subscriptions.json").exists()

    pending = _response()
    subscriptions = _subscriptions(pending)
    subscription = subscriptions[0]
    subscription["SubscriptionArn"] = "PendingConfirmation"
    _ = response_path.write_text(json.dumps(pending))
    environment["EXPECTED_ARN"] = "PendingConfirmation"

    rejected = subprocess.run(
        ["bash", "-c", command],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "budget alert subscription is not exactly confirmed" in rejected.stderr
    assert SUBSCRIBER_EMAIL not in rejected.stderr
    assert not (artifacts_path / "budget-subscriptions.json").exists()
