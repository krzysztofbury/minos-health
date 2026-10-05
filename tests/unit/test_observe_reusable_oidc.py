import base64
import json

import httpx
import pytest

from scripts.observe_reusable_oidc import (
    AUDIENCE,
    SUBJECT,
    RehearsalError,
    observe_claims,
    request_observation,
)


def sample_claims() -> dict[str, object]:
    return {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": AUDIENCE,
        "repository": "krzysztofbury/minos-health",
        "repository_id": "1325001960",
        "repository_owner_id": "5102389",
        "repository_visibility": "private",
        "event_name": "pull_request",
        "runner_environment": "github-hosted",
        "sub": SUBJECT,
        "ref": "refs/pull/140/merge",
        "sha": "a" * 40,
        "job_workflow_ref": "krzysztofbury/minos-health/.github/workflows/freshness-oidc-reusable.yml@"
        + "b" * 40,
        "job_workflow_sha": "b" * 40,
        "run_id": "123456",
        "run_attempt": "1",
        "iat": 100,
        "nbf": 100,
        "exp": 400,
        "private_unknown_claim": "PRIVATE_SENTINEL",
    }


def token(claims: dict[str, object]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "syntheticheader." + payload + ".syntheticsignature"


def context() -> dict[str, str]:
    return {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REPOSITORY_ID": "1325001960",
        "REHEARSAL_HEAD_REPOSITORY_ID": "1325001960",
        "GITHUB_BASE_REF": "main",
        "GITHUB_REF": "refs/pull/140/merge",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://runner.actions.githubusercontent.com/oidc?api-version=2",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "PRIVATE_BEARER_SENTINEL",
    }


def test_observation_is_allowlisted_unverified_and_never_contains_tokens() -> None:
    raw = token(sample_claims())
    result = observe_claims(raw, now_seconds=150)
    assert result["status"] == "unverified_oidc_claim_observation"
    claims = result["claims"]
    assert isinstance(claims, dict)
    assert claims["repository_id"] == "1325001960"
    assert claims["job_workflow_sha"] == "b" * 40
    rendered = json.dumps(result)
    assert raw not in rendered
    assert "PRIVATE_" not in rendered
    assert "syntheticsignature" not in rendered


def test_name_only_subject_is_reported_as_a_mismatch_not_an_authority_fallback() -> (
    None
):
    claims = sample_claims()
    claims["sub"] = "repo:krzysztofbury/minos-health:pull_request"
    result = observe_claims(token(claims), now_seconds=150)
    assert result["immutable_subject_matches_expected"] is False
    assert result["status"] == "unverified_oidc_claim_observation"


def test_custom_subject_is_reported_only_when_paired_with_exact_reusable_workflow() -> (
    None
):
    claims = sample_claims()
    assert (
        observe_claims(token(claims), now_seconds=150)[
            "subject_binds_exact_reusable_workflow"
        ]
        is False
    )
    claims["sub"] = SUBJECT + ":job_workflow_ref:" + str(claims["job_workflow_ref"])
    result = observe_claims(token(claims), now_seconds=150)
    assert result["subject_binds_exact_reusable_workflow"] is True
    assert result["immutable_subject_matches_expected"] is True
    assert result["status"] == "unverified_oidc_claim_observation"
    claims["sub"] = (
        SUBJECT
        + ":job_workflow_ref:"
        + str(claims["job_workflow_ref"]).replace("b" * 40, "c" * 40)
    )
    with pytest.raises(RehearsalError, match="claims are unsupported"):
        _ = observe_claims(token(claims), now_seconds=150)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aud", "sts.amazonaws.com"),
        ("repository_id", "98765"),
        ("repository_owner_id", "98765"),
        ("repository_visibility", "public"),
        ("event_name", "pull_request_target"),
        ("runner_environment", "self-hosted"),
        ("sub", "repo:krzysztofbury/minos-health:ref:refs/heads/main"),
        ("ref", "refs/heads/main"),
        ("job_workflow_sha", "c" * 40),
        (
            "job_workflow_ref",
            "krzysztofbury/minos-health/.github/workflows/freshness-oidc-reusable.yml@main",
        ),
        ("exp", 90),
    ],
)
def test_foreign_or_mutable_claim_samples_are_refused_without_echoing(
    field: str, value: object
) -> None:
    claims = sample_claims()
    claims[field] = value
    with pytest.raises(RehearsalError, match="claims are unsupported") as failure:
        _ = observe_claims(token(claims), now_seconds=150)
    assert "PRIVATE_" not in str(failure.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_EVENT_NAME", "pull_request_target"),
        ("REHEARSAL_HEAD_REPOSITORY_ID", "999"),
        ("GITHUB_BASE_REF", "other"),
        ("GITHUB_REF", "refs/tags/v1"),
    ],
)
def test_unsupported_context_is_refused_before_requesting_any_token(
    field: str, value: str
) -> None:
    environment = context()
    environment[field] = value

    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("unsupported context reached the token service")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RehearsalError, match="context is unsupported"):
            _ = request_observation(client, environment)


def test_request_uses_noncloud_audience_and_preserves_service_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    monkeypatch.setattr(time, "time", lambda: 150.0)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api-version"] == "2"
        assert request.url.params["audience"] == "minos-health-nonprivileged-rehearsal"
        assert request.headers["authorization"] == "Bearer PRIVATE_BEARER_SENTINEL"
        return httpx.Response(200, json={"value": token(sample_claims())})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = request_observation(client, context())
    assert result["status"] == "unverified_oidc_claim_observation"
    assert "PRIVATE_" not in json.dumps(result)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/oidc",
        "http://runner.actions.githubusercontent.com/oidc",
        "https://runner.actions.githubusercontent.com.evil.example/oidc",
        "https://runner.actions.githubusercontent.com:PRIVATE_SECRET/oidc",
    ],
)
def test_request_never_sends_bearer_to_unapproved_host(url: str) -> None:
    environment = context()
    environment["ACTIONS_ID_TOKEN_REQUEST_URL"] = url

    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("bearer could leave the approved GitHub service")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RehearsalError, match="configuration is unsupported"):
            _ = request_observation(client, environment)


@pytest.mark.parametrize(
    ("status", "data", "message"),
    [
        (302, b"", "request was refused"),
        (200, b"PRIVATE_" * 10000, "exceeds its bound"),
    ],
)
def test_redirect_is_not_followed_and_large_response_is_refused(
    status: int, data: bytes, message: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status, content=data, headers={"Location": "https://evil.example/"}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RehearsalError, match=message):
            _ = request_observation(client, context())
    assert len(requests) == 1
