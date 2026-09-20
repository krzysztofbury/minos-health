import base64
import binascii
import math
import os
import re
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Final, Protocol, final

# Subprocess calls are restricted to this absolute system path.
SSH_KEYGEN_PATH: Final = "/usr/bin/ssh-keygen"
RECOVERY_INVENTORY_NAMESPACE: Final = "minos-health-recovery-inventory-v1"
_MAX_SIGNED_BYTES = 8 * 1024
_MAX_SIGNATURE_BYTES = 4 * 1024
_MAX_PUBLIC_KEY_BYTES = 1024
_SIGNER_IDENTITY = re.compile(r"\A[A-Za-z0-9._@+-]{1,128}\Z")
_SIGNATURE = re.compile(
    rb"\A-----BEGIN SSH SIGNATURE-----\n"
    + rb"[A-Za-z0-9+/=\n]+"
    + rb"-----END SSH SIGNATURE-----\n\Z"
)


class InventorySigningError(RuntimeError):
    """Recovery inventory signing or verification failed safely."""


class InventorySigner(Protocol):
    def sign(self, content: bytes) -> str: ...


class InventoryVerifier(Protocol):
    def verify(self, content: bytes, signature: str) -> bool: ...


@final
class SshKeygenEd25519Signer:
    __slots__ = ("_private_key_file", "_timeout_seconds")

    def __init__(
        self,
        private_key_file: str | Path,
        *,
        timeout_seconds: float = 15,
    ) -> None:
        _validate_timeout(timeout_seconds)
        self._private_key_file = Path(private_key_file)
        self._timeout_seconds = timeout_seconds

    def sign(self, content: bytes) -> str:
        _validate_content(content)
        public_key = _run_ssh_keygen(
            (SSH_KEYGEN_PATH, "-y", "-f", str(self._private_key_file)),
            b"",
            self._timeout_seconds,
        )
        _ = _validate_ed25519_public_key(public_key)
        signature = _run_ssh_keygen(
            (
                SSH_KEYGEN_PATH,
                "-Y",
                "sign",
                "-f",
                str(self._private_key_file),
                "-n",
                RECOVERY_INVENTORY_NAMESPACE,
            ),
            content,
            self._timeout_seconds,
        )
        return _decode_signature(signature)


@final
class SshKeygenEd25519Verifier:
    __slots__ = ("_public_key", "_signer_identity", "_timeout_seconds")

    def __init__(
        self,
        public_key: str,
        *,
        signer_identity: str = "minos-recovery-operator",
        timeout_seconds: float = 15,
    ) -> None:
        _validate_timeout(timeout_seconds)
        if _SIGNER_IDENTITY.fullmatch(signer_identity) is None:
            raise InventorySigningError("recovery signer identity is invalid")
        try:
            encoded_key = public_key.encode("ascii", errors="strict")
        except UnicodeEncodeError:
            raise InventorySigningError("recovery public key is invalid") from None
        self._public_key = _validate_ed25519_public_key(encoded_key)
        self._signer_identity = signer_identity
        self._timeout_seconds = timeout_seconds

    def verify(self, content: bytes, signature: str) -> bool:
        _validate_content(content)
        signature_bytes = _encode_signature(signature)
        try:
            with tempfile.TemporaryDirectory(prefix="minos-recovery-verify-") as name:
                directory = Path(name)
                allowed_signers = directory / "allowed-signers"
                signature_file = directory / "inventory.sig"
                _ = allowed_signers.write_bytes(
                    self._signer_identity.encode("ascii")
                    + b" "
                    + self._public_key
                    + b"\n"
                )
                _ = signature_file.write_bytes(signature_bytes)
                os.chmod(allowed_signers, 0o600)
                os.chmod(signature_file, 0o600)
                result = subprocess.run(  # nosec B603
                    (
                        SSH_KEYGEN_PATH,
                        "-Y",
                        "verify",
                        "-f",
                        str(allowed_signers),
                        "-I",
                        self._signer_identity,
                        "-n",
                        RECOVERY_INVENTORY_NAMESPACE,
                        "-s",
                        str(signature_file),
                    ),
                    input=content,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    timeout=self._timeout_seconds,
                )
        except OSError, subprocess.SubprocessError:
            raise InventorySigningError(
                "recovery inventory verification failed"
            ) from None
        return result.returncode == 0


def _run_ssh_keygen(
    command: tuple[str, ...], content: bytes, timeout_seconds: float
) -> bytes:
    try:
        result = subprocess.run(  # nosec B603
            command,
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            timeout=timeout_seconds,
        )
    except OSError, subprocess.SubprocessError:
        raise InventorySigningError("recovery inventory signing failed") from None
    if result.returncode != 0:
        raise InventorySigningError("recovery inventory signing failed")
    return result.stdout


def _validate_ed25519_public_key(public_key: bytes) -> bytes:
    if len(public_key) < 1 or len(public_key) > _MAX_PUBLIC_KEY_BYTES:
        raise InventorySigningError("recovery public key is invalid")
    fields = public_key.strip().split()
    if len(fields) < 2 or fields[0] != b"ssh-ed25519":
        raise InventorySigningError("recovery public key is not Ed25519")
    try:
        decoded = base64.b64decode(fields[1], validate=True)
    except binascii.Error, ValueError:
        raise InventorySigningError("recovery public key is invalid") from None
    if len(decoded) != 51:
        raise InventorySigningError("recovery public key is invalid")
    if decoded[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 ":
        raise InventorySigningError("recovery public key is invalid")
    canonical = b"ssh-ed25519 " + fields[1]
    return canonical


def _decode_signature(signature: bytes) -> str:
    if len(signature) < 1 or len(signature) > _MAX_SIGNATURE_BYTES:
        raise InventorySigningError("recovery inventory signature is invalid")
    if _SIGNATURE.fullmatch(signature) is None:
        raise InventorySigningError("recovery inventory signature is invalid")
    try:
        return signature.decode("ascii")
    except UnicodeDecodeError:
        raise InventorySigningError("recovery inventory signature is invalid") from None


def _encode_signature(signature: str) -> bytes:
    try:
        encoded = signature.encode("ascii")
    except UnicodeEncodeError:
        raise InventorySigningError("recovery inventory signature is invalid") from None
    if len(encoded) > _MAX_SIGNATURE_BYTES or _SIGNATURE.fullmatch(encoded) is None:
        raise InventorySigningError("recovery inventory signature is invalid")
    return encoded


def _validate_content(content: bytes) -> None:
    if len(content) < 1:
        raise InventorySigningError("recovery inventory content is invalid")
    if len(content) > _MAX_SIGNED_BYTES:
        raise InventorySigningError("recovery inventory content exceeds its bound")


def _validate_timeout(timeout_seconds: object) -> None:
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > 300
    ):
        raise InventorySigningError("recovery signing timeout is invalid")
