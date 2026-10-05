import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from minos.ingest.processing.release_authority import (
    AuthorityError,
    DataDelegation,
    ScopeRequest,
    SignatureEnvelope,
    SignedManifest,
    verify_manifest_scope,
)
from minos.store.artifact import canonical_json

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def sign(key: Path, content: bytes, namespace: str) -> SignatureEnvelope:
    result = subprocess.run(
        ["/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace],
        input=content,
        capture_output=True,
        check=True,
        timeout=15,
    )
    return SignatureEnvelope(content=content, signature=result.stdout.decode("ascii"))


@pytest.fixture
def keys(tmp_path: Path) -> tuple[Path, str, Path, str]:
    public: list[str] = []
    for name in ("owner-fixture", "delegate-fixture"):
        private = tmp_path / name
        _ = subprocess.run(
            [
                "/usr/bin/ssh-keygen",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "",
                "-f",
                str(private),
            ],
            capture_output=True,
            check=True,
            timeout=15,
        )
        public.append(private.with_suffix(".pub").read_text().strip())
    return (
        tmp_path / "owner-fixture",
        public[0],
        tmp_path / "delegate-fixture",
        public[1],
    )


def baseline_model() -> SignedManifest:
    return SignedManifest.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "owner_baseline",
                "tag": "v1-fixture",
                "source_revision": "a" * 40,
                "runtime": {
                    "api": "sha256:" + "a" * 64,
                    "edge": "sha256:" + "b" * 64,
                    "watcher": "sha256:" + "c" * 64,
                    "cloudflared": "sha256:" + "d" * 64,
                    **{
                        name: "e" * 64
                        for name in (
                            "api_non_data_sha256",
                            "build_profile_sha256",
                            "transform_profile_sha256",
                            "compatibility_sha256",
                            "static_sha256",
                            "cli_sha256",
                            "schema_sha256",
                        )
                    },
                },
                **{
                    name: "f" * 64
                    for name in (
                        "dataset_sha256",
                        "database_sha256",
                        "data_bundle_sha256",
                        "candidate_audit_sha256",
                        "gate_attestations_sha256",
                        "source_profile_sha256",
                        "archive_selection_sha256",
                        "predecessor_manifest_sha256",
                        "predecessor_receipt_sha256",
                    )
                },
                "baseline_manifest_sha256": None,
                "delegation_sha256": None,
            }
        )
    )


def delegation_model(baseline: SignatureEnvelope, delegate_key: str) -> DataDelegation:
    return DataDelegation(
        schema_version=1,
        purpose="routine_data",
        generation=1,
        certificate_id="a" * 64,
        baseline_manifest_sha256=hashlib.sha256(baseline.content).hexdigest(),
        delegate_public_key=delegate_key,
        policy_sha256="b" * 64,
        authority_scope_sha256="c" * 64,
        source_profiles=("f" * 64,),
        data_tag_prefix="vdata-fixture-",
        not_before=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        maximum_sequence=10,
    )


def request_model() -> ScopeRequest:
    return ScopeRequest(
        generation=1,
        policy_sha256="b" * 64,
        authority_scope_sha256="c" * 64,
        predecessor_manifest_sha256="f" * 64,
        predecessor_receipt_sha256="f" * 64,
        sequence=1,
        now=NOW,
    )


def candidate_model(
    baseline: SignatureEnvelope, delegation: SignatureEnvelope
) -> SignedManifest:
    raw = baseline_model().model_dump(mode="json")
    raw.update(
        {
            "kind": "routine_data",
            "tag": "vdata-fixture-001",
            "source_revision": "b" * 40,
            "baseline_manifest_sha256": hashlib.sha256(baseline.content).hexdigest(),
            "delegation_sha256": hashlib.sha256(delegation.content).hexdigest(),
            "database_sha256": "a" * 64,
            "dataset_sha256": "b" * 64,
            "data_bundle_sha256": "c" * 64,
        }
    )
    raw["runtime"]["api"] = "sha256:" + "e" * 64
    return SignedManifest.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "owner-signature",
        "delegate-signature",
        "namespace",
        "same-key",
        "baseline",
        "delegation",
        "runtime",
        "source-disabled",
        "tag",
        "expired",
        "not-yet-valid",
        "generation",
        "policy",
        "sequence",
        "predecessor",
        "duplicate-json",
        "noncanonical-json",
    ],
)
def test_real_signatures_bind_scope_without_authorizing_activation(
    keys: tuple[Path, str, Path, str],
    damage: str,
) -> None:
    owner, owner_public, delegate, delegate_public = keys
    baseline = sign(owner, canonical_json(baseline_model()), "minos-release")
    certificate = delegation_model(
        baseline, owner_public if damage == "same-key" else delegate_public
    )
    if damage == "source-disabled":
        certificate = DataDelegation.model_validate(
            {**certificate.model_dump(), "source_profiles": ()}
        )
    delegation = sign(owner, canonical_json(certificate), "minos-data-delegation-v1")
    release = candidate_model(baseline, delegation)
    raw = release.model_dump(mode="json")
    if damage in ("baseline", "delegation"):
        raw[
            f"{damage}_manifest_sha256" if damage == "baseline" else "delegation_sha256"
        ] = "0" * 64
    elif damage == "runtime":
        raw["runtime"]["edge"] = "sha256:" + "0" * 64
    elif damage == "tag":
        raw["tag"] = "v2-code-release"
    elif damage == "predecessor":
        raw["predecessor_receipt_sha256"] = "0" * 64
    release = SignedManifest.model_validate_json(json.dumps(raw))
    content = canonical_json(release)
    if damage == "duplicate-json":
        assert b'"schema_version":1,' in content
        content = content.replace(
            b'"schema_version":1,', b'"schema_version":1,"schema_version":1,'
        )
    elif damage == "noncanonical-json":
        content += b" "
    candidate = sign(
        owner if damage == "same-key" else delegate,
        content,
        "minos-deployment" if damage == "namespace" else "minos-release",
    )
    if damage == "owner-signature":
        baseline = sign(delegate, baseline.content, "minos-release")
    elif damage == "delegate-signature":
        candidate = sign(owner, candidate.content, "minos-release")
    request = request_model()
    changes: dict[str, dict[str, object]] = {
        "expired": {"now": certificate.expires_at},
        "not-yet-valid": {"now": certificate.not_before - timedelta(seconds=1)},
        "generation": {"generation": 2},
        "policy": {"policy_sha256": "0" * 64},
        "sequence": {"sequence": 11},
    }
    request = ScopeRequest.model_validate(
        {**request.model_dump(), **changes.get(damage, {})}
    )
    if damage == "none":
        result = verify_manifest_scope(
            owner_public_key=owner_public,
            baseline=baseline,
            delegation=delegation,
            candidate=candidate,
            request=request,
        )
        assert result.status == "signature_and_scope_only"
        assert result.manifest_sha256 == hashlib.sha256(candidate.content).hexdigest()
        assert result.manifest.runtime.api != baseline_model().runtime.api
        assert result.manifest.database_sha256 == "a" * 64
    else:
        with pytest.raises(AuthorityError):
            _ = verify_manifest_scope(
                owner_public_key=owner_public,
                baseline=baseline,
                delegation=delegation,
                candidate=candidate,
                request=request,
            )


def test_manifest_and_certificate_inputs_fail_closed(
    keys: tuple[Path, str, Path, str],
) -> None:
    owner, _, _, public = keys
    baseline = sign(owner, canonical_json(baseline_model()), "minos-release")
    certificate = delegation_model(baseline, public)
    with pytest.raises(ValidationError):
        _ = DataDelegation.model_validate(
            {**certificate.model_dump(), "generation": True}
        )
    with pytest.raises(ValidationError):
        _ = DataDelegation.model_validate(
            {**certificate.model_dump(), "expires_at": certificate.not_before}
        )
    with pytest.raises(ValidationError):
        _ = SignatureEnvelope(content=b"x" * 65537, signature="fixture")
    raw = baseline_model().model_dump()
    raw["kind"] = "routine_data"
    with pytest.raises(ValidationError):
        _ = SignedManifest.model_validate(raw)
