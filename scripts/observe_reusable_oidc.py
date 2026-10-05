"""Observe allowlisted GitHub claims only; never verify provenance or assume a role."""

import base64
import binascii
import json
import os
import re
import sys
import time
from collections.abc import Mapping
from typing import Annotated, ClassVar, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

AUDIENCE = "minos-health-nonprivileged-rehearsal"
REPOSITORY_ID = "1325001960"
SUBJECT = "repo:krzysztofbury@5102389/minos-health@1325001960:pull_request"
MAX_RESPONSE_BYTES = 64 * 1024
WORKFLOW_PREFIX = (
    "krzysztofbury/minos-health/.github/workflows/freshness-oidc-reusable.yml@"
)
type Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class RehearsalError(RuntimeError):
    """Only fixed safe diagnostics, never remote content or exception text."""


class _Claims(BaseModel):
    # Other GitHub claims are intentionally neither retained nor logged.
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", strict=True, frozen=True
    )
    iss: Literal["https://token.actions.githubusercontent.com"]
    aud: Literal["minos-health-nonprivileged-rehearsal"]
    repository: Literal["krzysztofbury/minos-health"]
    repository_id: Literal["1325001960"]
    repository_owner_id: Literal["5102389"]
    repository_visibility: Literal["private"]
    event_name: Literal["pull_request"]
    runner_environment: Literal["github-hosted"]
    sub: Annotated[
        str,
        Field(
            pattern=(
                r"^repo:krzysztofbury(?:@5102389)?/minos-health(?:@1325001960)?:pull_request"
                + r"(?::job_workflow_ref:krzysztofbury/minos-health/\.github/workflows/freshness-oidc-reusable\.yml@[0-9a-f]{40})?$"
            ),
        ),
    ]
    ref: Annotated[str, Field(pattern=r"^refs/pull/[1-9][0-9]*/merge$")]
    sha: Sha
    job_workflow_ref: Annotated[
        str,
        Field(
            pattern=r"^krzysztofbury/minos-health/\.github/workflows/freshness-oidc-reusable\.yml@[0-9a-f]{40}$"
        ),
    ]
    job_workflow_sha: Sha
    run_id: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,19}$")]
    run_attempt: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,5}$")]
    iat: int
    nbf: int
    exp: int

    @model_validator(mode="after")
    def require_consistent_sample(self) -> Self:
        if self.job_workflow_ref != WORKFLOW_PREFIX + self.job_workflow_sha:
            raise ValueError("reusable workflow claims disagree")
        if ":job_workflow_ref:" in self.sub and not self.sub.endswith(
            ":job_workflow_ref:" + self.job_workflow_ref
        ):
            raise ValueError("subject and reusable workflow claims disagree")
        if not self.nbf <= self.iat < self.exp or self.exp - self.iat > 3600:
            raise ValueError("token sample validity is unsupported")
        return self


class _TokenResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    value: str = Field(min_length=1, max_length=MAX_RESPONSE_BYTES)


def observe_claims(token: str, *, now_seconds: int) -> dict[str, object]:
    """Decode for diagnostics only. The JWT signature is deliberately not verified."""
    if len(token) > MAX_RESPONSE_BYTES:
        raise RehearsalError("OIDC sample exceeds its bound")
    segments = token.split(".")
    if len(segments) != 3 or any(
        re.fullmatch(r"[A-Za-z0-9_-]+", part) is None for part in segments
    ):
        raise RehearsalError("OIDC sample encoding is unsupported")
    try:
        payload = base64.b64decode(
            segments[1] + "=" * (-len(segments[1]) % 4), altchars=b"-_", validate=True
        )
        claims = _Claims.model_validate_json(payload)
    except ValueError, binascii.Error, ValidationError:
        raise RehearsalError("OIDC sample claims are unsupported") from None
    if not claims.nbf - 60 <= now_seconds < claims.exp or claims.iat > now_seconds + 60:
        raise RehearsalError("OIDC sample validity window is unsupported")
    # Never return a JWT, bearer token, arbitrary extra claim or trusted verdict.
    return {
        "status": "unverified_oidc_claim_observation",
        "immutable_subject_matches_expected": claims.sub
        in (SUBJECT, SUBJECT + ":job_workflow_ref:" + claims.job_workflow_ref),
        "subject_binds_exact_reusable_workflow": claims.sub
        == SUBJECT + ":job_workflow_ref:" + claims.job_workflow_ref,
        "claims": claims.model_dump(),
    }


def require_rehearsal_context(environment: Mapping[str, str]) -> None:
    if (
        environment.get("GITHUB_EVENT_NAME") != "pull_request"
        or environment.get("GITHUB_REPOSITORY_ID") != REPOSITORY_ID
        or environment.get("REHEARSAL_HEAD_REPOSITORY_ID") != REPOSITORY_ID
        or environment.get("GITHUB_BASE_REF") != "main"
        or re.fullmatch(
            r"refs/pull/[1-9][0-9]*/merge", environment.get("GITHUB_REF", "")
        )
        is None
    ):
        raise RehearsalError("OIDC rehearsal context is unsupported")


def request_observation(
    client: httpx.Client, environment: Mapping[str, str]
) -> dict[str, object]:
    require_rehearsal_context(environment)
    raw_url = environment.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    bearer = environment.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    if len(raw_url) > 8192:
        raise RehearsalError("OIDC rehearsal request configuration is unsupported")
    try:
        url = httpx.URL(raw_url)
    except httpx.InvalidURL:
        raise RehearsalError(
            "OIDC rehearsal request configuration is unsupported"
        ) from None
    if (
        url.scheme != "https"
        or not url.host.endswith(".actions.githubusercontent.com")
        or url.port not in (None, 443)
        or url.username
        or url.password
        or url.fragment
        or not 1 <= len(bearer) <= 8192
        or not bearer.isascii()
        or any(ord(character) < 33 or ord(character) > 126 for character in bearer)
    ):
        raise RehearsalError("OIDC rehearsal request configuration is unsupported")
    url = url.copy_set_param("audience", AUDIENCE)
    deadline = time.monotonic() + 20
    with client.stream(
        "GET",
        url,
        headers={"Authorization": f"Bearer {bearer}"},
        follow_redirects=False,
    ) as response:
        if response.status_code != 200:
            raise RehearsalError("OIDC rehearsal request was refused")
        payload = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            if (
                len(payload) + len(chunk) > MAX_RESPONSE_BYTES
                or time.monotonic() >= deadline
            ):
                raise RehearsalError("OIDC rehearsal response exceeds its bound")
            payload.extend(chunk)
        token = _TokenResponse.model_validate_json(payload).value
    return observe_claims(token, now_seconds=int(time.time()))


def main() -> int:
    try:
        with httpx.Client(
            timeout=10, trust_env=False, follow_redirects=False
        ) as client:
            result = request_observation(client, os.environ)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except RehearsalError as error:
        message = str(error)
    except httpx.HTTPError, OSError, ValidationError, ValueError:
        message = "OIDC rehearsal response could not be observed"
    print(
        json.dumps(
            {
                "status": "refused",
                "error": {"code": "oidc_rehearsal_failed", "message": message},
            }
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
