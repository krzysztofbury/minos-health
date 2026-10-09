"""Fetch only checksum-pinned public search-evaluation inputs into a new archive."""

import argparse
import hashlib
import re
from pathlib import Path
from typing import Protocol, final

import httpx

from minos.ingest.reference_manifest import DatasetManifest
from scripts.compose_conditions_manifest import bounded_read

_ALLOWED_URL = re.compile(
    r"https://(?:"
    + r"slowniki\.nfz\.gov\.pl/WersjeSlownikow/PobierzXml\?kodSlownika=ICD9&nrGen=[1-9][0-9]*"
    + r"|www\.nfz\.gov\.pl/download/gfx/nfz/pl/(?:defaultstronaopisowa|defaultaktualnosci)/[0-9/]+/[A-Za-z0-9_.-]+\.(?:xlsx|docx)"
    + r"|baw\.nfz\.gov\.pl/api/file/4/[1-9][0-9]*"
    + r"|rsk3\.ezdrowie\.gov\.pl/api/rsk/file/get/[a-f0-9-]{36}"
    + r"|api\.sejm\.gov\.pl/eli/acts/DU/20[0-9]{2}/[1-9][0-9]{0,4}/text\.pdf"
    + r")"
)


class SourcePin(Protocol):
    @property
    def source_url(self) -> str: ...

    @property
    def sha256(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...


def fetch_pin(client: httpx.Client, pin: SourcePin, directory: Path) -> None:
    """A changed upstream response fails closed; never refresh a reviewed pin."""
    if _ALLOWED_URL.fullmatch(pin.source_url) is None:
        raise ValueError("evaluation source endpoint is not allowed")
    if re.fullmatch(r"[a-f0-9]{64}", pin.sha256) is None:
        raise ValueError("invalid evaluation source digest")
    if not 1 <= pin.size_bytes <= 64 * 1024 * 1024:
        raise ValueError("evaluation source exceeds size bound")
    destination = directory / pin.sha256
    digest = hashlib.sha256()
    size_bytes = 0
    # Exclusive creation happens outside cleanup: a preexisting path is never
    # ours to unlink, even if opening it fails with a permission error.
    with destination.open("xb") as target:
        try:
            with client.stream(
                "GET", pin.source_url, follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise ValueError("evaluation source is unavailable or redirects")
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > pin.size_bytes:
                        raise ValueError("evaluation source exceeds its pinned size")
                    digest.update(chunk)
                    _ = target.write(chunk)
            if size_bytes != pin.size_bytes or digest.hexdigest() != pin.sha256:
                raise ValueError("evaluation source conflicts with its reviewed pin")
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def fetch_manifest(manifest: DatasetManifest, archive: Path) -> None:
    if manifest.modifiers or manifest.modifier_bindings:
        raise ValueError("search evaluation does not fetch legal binding inputs")
    pins: list[SourcePin] = list(manifest.sources)
    for notice in manifest.komunikaty:
        pins.extend((notice.algorithm, notice.ranks, notice.specialties))
    pins.extend(catalog.workbook for catalog in manifest.catalogs)
    pins.extend(conditions.workbook for conditions in manifest.characteristics)
    if manifest.grouping_dictionaries is not None:
        pins.append(manifest.grouping_dictionaries.specialties.source)
        pins.append(manifest.grouping_dictionaries.discharge_modes.source)
    # Byte-identical product workbooks recur across acts under other BAW file
    # IDs; fetch each content address once. Only those repeats are folded: any
    # other repeated address still fails the uniqueness check below.
    products: dict[str, SourcePin] = {}
    for catalogs in manifest.product_catalogs:
        for item in catalogs.files:
            seen = products.setdefault(item.sha256, item)
            if seen.size_bytes != item.size_bytes:
                raise ValueError("product catalog pins conflict for one digest")
    pins.extend(products.values())
    if len(pins) > 64 or len({pin.sha256 for pin in pins}) != len(pins):
        raise ValueError("evaluation source inventory exceeds its unique bound")
    archive.mkdir(parents=True, exist_ok=False)
    directory = archive / "blobs/sha256"
    directory.mkdir(parents=True)
    with httpx.Client(timeout=30, trust_env=False) as client:
        for pin in pins:
            fetch_pin(client, pin, directory)


@final
class Arguments(argparse.Namespace):
    manifest: Path = Path()
    archive_root: Path = Path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--manifest", type=Path, required=True)
    _ = parser.add_argument("--archive-root", type=Path, required=True)
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    try:
        manifest = DatasetManifest.model_validate_json(bounded_read(args.manifest))
        fetch_manifest(manifest, args.archive_root)
    except ValueError, httpx.HTTPError, OSError:
        # HTTP exception strings can contain URLs. Report no source bodies,
        # signed locations, filesystem paths or authentication information.
        parser.exit(2, "Pinned evaluation sources could not be verified.\n")


if __name__ == "__main__":
    main()
