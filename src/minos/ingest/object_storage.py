"""Cloudflare R2 through its S3 API, the only object store Minos uses.

R2 has no object versions and ignores bucket-owner headers. Archive keys
embed the SHA-256 of their bytes, writes are create-once (`If-None-Match:
*`) and bucket lock rules refuse deletes and overwrites, so a key plus its
verified SHA-256 and size is the complete object identity.
"""

import re

R2_REGION = "auto"
_R2_ENDPOINT = re.compile(
    r"https://[0-9a-f]{32}(?:\.(?:eu|fedramp))?\.r2\.cloudflarestorage\.com"
)


def validate_r2_endpoint(value: str) -> str:
    """Accept only an account S3 endpoint; never a custom or public domain."""
    if _R2_ENDPOINT.fullmatch(value) is None:
        raise ValueError("R2 endpoint must be an account S3 API endpoint")
    return value
