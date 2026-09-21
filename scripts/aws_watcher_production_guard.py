from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

MAX_RESPONSE_BYTES = 65_536


class ProductionGuardError(ValueError):
    pass


def validate_sns_subscription(
    document: object,
    *,
    topic_arn: str,
    subscriber_email: str,
    expected_subscription_arn: str,
) -> None:
    response = _mapping(document, "SNS response")
    next_token = response.get("NextToken")
    if next_token not in (None, ""):
        raise ProductionGuardError("SNS response is paginated")

    subscriptions = _sequence(response.get("Subscriptions"), "Subscriptions")
    if len(subscriptions) != 1:
        raise ProductionGuardError("SNS response must contain exactly one subscription")

    subscription = _mapping(subscriptions[0], "subscription")
    expected = {
        "TopicArn": topic_arn,
        "Protocol": "email",
        "Endpoint": subscriber_email,
        "SubscriptionArn": expected_subscription_arn,
    }
    for field, value in expected.items():
        if subscription.get(field) != value:
            raise ProductionGuardError(f"SNS subscription {field} does not match")

    if expected_subscription_arn == "PendingConfirmation":
        raise ProductionGuardError("SNS subscription is pending confirmation")
    if not expected_subscription_arn.startswith(f"{topic_arn}:"):
        raise ProductionGuardError("SNS subscription ARN is invalid for the topic")


def load_bounded_json(path: Path) -> object:
    with path.open("rb") as stream:
        payload = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ProductionGuardError("SNS response exceeds the configured bound")
    try:
        return cast(object, json.loads(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductionGuardError("SNS response is not valid JSON") from error


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ProductionGuardError(f"{label} must be an object")
    untyped = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in untyped):
        raise ProductionGuardError(f"{label} keys must be strings")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ProductionGuardError(f"{label} must be an array")
    return cast(Sequence[object], value)


def main(arguments: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) != 4:
        print(
            "usage: aws_watcher_production_guard.py RESPONSE TOPIC EMAIL SUBSCRIPTION_ARN",
            file=sys.stderr,
        )
        return 2
    try:
        validate_sns_subscription(
            load_bounded_json(Path(values[0])),
            topic_arn=values[1],
            subscriber_email=values[2],
            expected_subscription_arn=values[3],
        )
    except (OSError, ProductionGuardError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
