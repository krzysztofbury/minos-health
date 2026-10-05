"""Offline signed-manifest scope verification, not deployment authorization."""

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self, cast

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from minos.ingest.manifest_types import Digest
from minos.store.artifact import canonical_json

MAX_SIGNED_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 4096
SSH_KEYGEN = "/usr/bin/ssh-keygen"
type ImageDigest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
type Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
type Tag = Annotated[str, Field(pattern=r"^v[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
type Namespace = Literal["minos-release", "minos-data-delegation-v1"]


class AuthorityError(RuntimeError):
    """Fixed redacted diagnostics for an unverified release authority input."""


class _AuthorityModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class RuntimeIdentity(_AuthorityModel):
    api: ImageDigest
    edge: ImageDigest
    watcher: ImageDigest
    cloudflared: ImageDigest
    api_non_data_sha256: Digest
    build_profile_sha256: Digest
    transform_profile_sha256: Digest
    compatibility_sha256: Digest
    static_sha256: Digest
    cli_sha256: Digest
    schema_sha256: Digest


class SignedManifest(_AuthorityModel):
    schema_version: Literal[1]
    kind: Literal["owner_baseline", "routine_data"]
    tag: Tag
    source_revision: Revision
    runtime: RuntimeIdentity
    dataset_sha256: Digest
    database_sha256: Digest
    data_bundle_sha256: Digest
    candidate_audit_sha256: Digest
    gate_attestations_sha256: Digest
    source_profile_sha256: Digest
    archive_selection_sha256: Digest
    predecessor_manifest_sha256: Digest
    predecessor_receipt_sha256: Digest
    baseline_manifest_sha256: Digest | None
    delegation_sha256: Digest | None

    @model_validator(mode="after")
    def require_authority_binding(self) -> Self:
        if self.kind == "owner_baseline":
            if (
                self.baseline_manifest_sha256 is not None
                or self.delegation_sha256 is not None
            ):
                raise ValueError("owner baseline cannot claim delegated authority")
        elif self.baseline_manifest_sha256 is None or self.delegation_sha256 is None:
            raise ValueError("routine manifest requires baseline and delegation")
        return self


class DataDelegation(_AuthorityModel):
    schema_version: Literal[1]
    purpose: Literal["routine_data"]
    generation: int = Field(ge=1, le=2**63 - 1)
    certificate_id: Digest
    baseline_manifest_sha256: Digest
    delegate_public_key: str = Field(min_length=1, max_length=1024)
    policy_sha256: Digest
    authority_scope_sha256: Digest
    # Explicitly empty means disabled, never all source profiles.
    source_profiles: Annotated[tuple[Digest, ...], Field(max_length=128)]
    data_tag_prefix: str = Field(pattern=r"^v[A-Za-z0-9][A-Za-z0-9._-]{0,62}-$")
    not_before: AwareDatetime
    expires_at: AwareDatetime
    maximum_sequence: int = Field(ge=1, le=2**63 - 1)

    @model_validator(mode="after")
    def require_finite_unique_scope(self) -> Self:
        if self.expires_at <= self.not_before:
            raise ValueError("delegation validity interval is empty")
        if self.source_profiles != tuple(sorted(set(self.source_profiles))):
            raise ValueError("source profiles must be unique and sorted")
        _ = _public_key_bytes(self.delegate_public_key)
        return self


class SignatureEnvelope(_AuthorityModel):
    content: bytes = Field(min_length=1, max_length=MAX_SIGNED_BYTES)
    signature: str = Field(min_length=1, max_length=MAX_SIGNATURE_BYTES)


class ScopeRequest(_AuthorityModel):
    """Trusted caller pins. Freshness/chain proofs are a separate future boundary."""

    generation: int = Field(ge=1, le=2**63 - 1)
    policy_sha256: Digest
    authority_scope_sha256: Digest
    predecessor_manifest_sha256: Digest
    predecessor_receipt_sha256: Digest
    sequence: int = Field(ge=1, le=2**63 - 1)
    now: AwareDatetime


class ScopeVerification(_AuthorityModel):
    status: Literal["signature_and_scope_only"]
    manifest: SignedManifest
    manifest_sha256: Digest
    delegation_sha256: Digest


def verify_manifest_scope(
    *,
    owner_public_key: str,
    baseline: SignatureEnvelope,
    delegation: SignatureEnvelope,
    candidate: SignatureEnvelope,
    request: ScopeRequest,
) -> ScopeVerification:
    """No provenance/fresh-revocation/chain check, signing or activation authority.

    The result is intentionally not an authorization token. Callers must verify
    actual non-data contents and independent gate/provenance/authority evidence.
    """
    root = _verify_model(SignedManifest, baseline, owner_public_key, "minos-release")
    certificate = _verify_model(
        DataDelegation, delegation, owner_public_key, "minos-data-delegation-v1"
    )
    if _public_key_bytes(owner_public_key) == _public_key_bytes(
        certificate.delegate_public_key
    ):
        raise AuthorityError("delegated key is not independent")
    release = _verify_model(
        SignedManifest, candidate, certificate.delegate_public_key, "minos-release"
    )
    baseline_digest = hashlib.sha256(baseline.content).hexdigest()
    delegation_digest = hashlib.sha256(delegation.content).hexdigest()
    if root.kind != "owner_baseline" or release.kind != "routine_data":
        raise AuthorityError("release authority purpose differs")
    if (
        certificate.baseline_manifest_sha256 != baseline_digest
        or release.baseline_manifest_sha256 != baseline_digest
    ):
        raise AuthorityError("release baseline binding differs")
    if release.delegation_sha256 != delegation_digest:
        raise AuthorityError("release delegation binding differs")
    _check_scope(certificate, release, request)
    # An embedded-data API digest may differ; every non-data content/profile pin
    # stays equal. Actual filesystem/provenance proof is not supplied by labels.
    old = root.runtime.model_dump(exclude={"api"})
    new = release.runtime.model_dump(exclude={"api"})
    if old != new:
        raise AuthorityError("routine release changes its runtime baseline")
    return ScopeVerification(
        status="signature_and_scope_only",
        manifest=release,
        manifest_sha256=hashlib.sha256(candidate.content).hexdigest(),
        delegation_sha256=delegation_digest,
    )


def _check_scope(
    certificate: DataDelegation, release: SignedManifest, request: ScopeRequest
) -> None:
    if certificate.generation != request.generation:
        raise AuthorityError("delegation generation differs")
    if (
        certificate.policy_sha256 != request.policy_sha256
        or certificate.authority_scope_sha256 != request.authority_scope_sha256
    ):
        raise AuthorityError("delegation policy scope differs")
    if not certificate.not_before <= request.now < certificate.expires_at:
        raise AuthorityError("delegation is outside its validity interval")
    if request.sequence > certificate.maximum_sequence:
        raise AuthorityError("delegation sequence limit exceeded")
    if release.source_profile_sha256 not in certificate.source_profiles:
        raise AuthorityError("source profile is not delegated")
    if (
        not release.tag.startswith(certificate.data_tag_prefix)
        or release.tag == certificate.data_tag_prefix
    ):
        raise AuthorityError("release tag is outside delegated scope")
    if (
        release.predecessor_manifest_sha256 != request.predecessor_manifest_sha256
        or release.predecessor_receipt_sha256 != request.predecessor_receipt_sha256
    ):
        raise AuthorityError("release predecessor differs")


def _verify_model[T: BaseModel](
    model: type[T], envelope: SignatureEnvelope, key: str, namespace: Namespace
) -> T:
    _verify_signature(envelope, key, namespace)
    try:
        # Pydantic accepts duplicate JSON keys by default. Reject them before the
        # model/canonical comparison so a signature has just one interpretation.
        _ = cast(object, json.loads(envelope.content, object_pairs_hook=_unique_object))
        parsed = model.model_validate_json(envelope.content)
        if canonical_json(parsed) != envelope.content:
            raise AuthorityError("signed payload is not canonical")
        return parsed
    except ValueError, UnicodeError, RecursionError, ValidationError:
        raise AuthorityError("signed payload could not be validated") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityError("signed payload contains duplicate fields")
        result[key] = value
    return result


def _public_key_bytes(key: str) -> bytes:
    try:
        if not 1 <= len(key) <= 1024:
            raise ValueError
        fields = key.encode("ascii").split()
        if len(fields) != 2 or fields[0] != b"ssh-ed25519":
            raise ValueError
        decoded = base64.b64decode(fields[1], validate=True)
        if (
            len(decoded) != 51
            or decoded[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 "
        ):
            raise ValueError
        if base64.b64encode(decoded) != fields[1]:
            raise ValueError
        return b"ssh-ed25519 " + fields[1]
    except ValueError, UnicodeError, binascii.Error:
        raise AuthorityError("release verification key is invalid") from None


def _verify_signature(
    envelope: SignatureEnvelope, key: str, namespace: Namespace
) -> None:
    public_key = _public_key_bytes(key)
    try:
        signature = envelope.signature.encode("ascii")
        if (
            len(signature) > MAX_SIGNATURE_BYTES
            or re.fullmatch(
                rb"-----BEGIN SSH SIGNATURE-----\n[A-Za-z0-9+/=\n]+-----END SSH SIGNATURE-----\n",
                signature,
            )
            is None
        ):
            raise AuthorityError("release signature encoding is invalid")
        with tempfile.TemporaryDirectory(prefix=".minos-authority-") as name:
            directory = Path(name)
            signers = directory / "allowed-signers"
            signed = directory / "signature"
            _ = signers.write_bytes(b"minos-authority " + public_key + b"\n")
            _ = signed.write_bytes(signature)
            os.chmod(signers, 0o600)
            os.chmod(signed, 0o600)
            result = subprocess.run(  # nosec B603
                (
                    SSH_KEYGEN,
                    "-Y",
                    "verify",
                    "-f",
                    str(signers),
                    "-I",
                    "minos-authority",
                    "-n",
                    namespace,
                    "-s",
                    str(signed),
                ),
                input=envelope.content,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                check=False,
                timeout=15,
            )
            if result.returncode != 0:
                raise AuthorityError("release signature could not be verified")
    except OSError, UnicodeError, subprocess.SubprocessError:
        raise AuthorityError("release signature verification failed") from None
