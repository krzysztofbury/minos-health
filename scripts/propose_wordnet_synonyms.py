"""Generate proposed mappings from pinned Polish WordNet synsets for review.

Run inside agent-scratch with the dictionary-tools extra. Nothing generated here
is activated automatically: the reviewed manifest remains the runtime boundary.
"""

import argparse
import hashlib
import json
import os
import re
import tarfile
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast, final

import httpx
from pydantic import ValidationError

from minos.domain.normalization import normalize_description
from minos.domain.search import text_tokens
from minos.ingest.search_synonyms import SearchSynonym

URL = "https://github.com/omwn/omw-data/releases/download/v2.0/omw-pl-2.0.tar.xz"
SHA256 = "afe0034c25f7c8ff71390768ec238380086cf382164840a9e55237ec9e872c7b"
SIZE_BYTES = 1119884


class Synset(Protocol):
    def lemmas(self) -> list[str]: ...


class Wordnet(Protocol):
    def synset(self, identity: str) -> Synset: ...


class Wn(Protocol):
    def add(self, source: Path, *, progress_handler: None) -> None: ...
    def Wordnet(self, lexicon: str, *, expand: str) -> Wordnet: ...


def proposed_pairs(identity: str, terms: list[str], system: str) -> list[SearchSynonym]:
    if len(terms) > 32:
        raise ValueError("synset exceeds term bound")
    proposals: list[SearchSynonym] = []
    terms = sorted({normalize_description(term) for term in terms})
    for term in terms:
        for wording in terms:
            if term == wording or len(text_tokens(wording)) < len(text_tokens(term)):
                continue
            if set(text_tokens(term + " " + wording)) & {"bez", "nie", "typ", "typu"}:
                continue
            try:
                proposal = SearchSynonym.model_validate(
                    {
                        "id": identity
                        + "-"
                        + hashlib.sha256(
                            (system + term + "\0" + wording).encode()
                        ).hexdigest()[:16],
                        "version": 1,
                        "system": system,
                        "term": term,
                        "source_wording": wording,
                        "whole_query_only": True,
                        "source_url": "https://github.com/omwn/omw-data/releases/tag/v2.0",
                        "evidence": f"Same Polish synset {identity}; omw-pl:2.0 archive SHA-256 {SHA256}. Proposed linguistic candidate only. Review medical sense, source wording, scope and qualifiers before activation.",
                        "status": "proposed",
                    }
                )
            except ValidationError:
                continue
            proposals.append(proposal)
    return proposals


@final
class Arguments(argparse.Namespace):
    synset: list[str] = []
    system: str = ""
    output: Path = Path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--synset", action="append", required=True)
    _ = parser.add_argument("--system", choices=("icd10-pl", "icd9-pl"), required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    identities = args.synset
    if not 1 <= len(identities) <= 32 or any(
        re.fullmatch(r"omw-pl-[0-9]{8}-[nvar]", value) is None for value in identities
    ):
        raise ValueError("invalid or oversized synset selection")
    root = Path(os.environ["AGENT_SCRATCH_DIR"])
    archive = root / "omw-pl-2.0.tar.xz"
    with (
        httpx.Client(timeout=60, follow_redirects=True) as client,
        client.stream("GET", URL) as response,
        archive.open("xb") as target,
    ):
        _ = response.raise_for_status()
        size = 0
        for block in response.iter_bytes(65536):
            size += len(block)
            if size > SIZE_BYTES:
                raise ValueError("archive exceeds pinned size")
            _ = target.write(block)
    if size != SIZE_BYTES or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError("WordNet archive identity mismatch")
    os.environ["WN_DATA_DIR"] = str(root / "wn-data")
    wn = cast(Wn, cast(object, import_module("wn")))
    wn.add(archive, progress_handler=None)
    wordnet = wn.Wordnet("omw-pl:2.0", expand="")
    proposals = [
        row
        for identity in sorted(set(identities))
        for row in proposed_pairs(
            identity, wordnet.synset(identity).lemmas(), args.system
        )
    ]
    if len(proposals) > 500:
        raise ValueError("candidate selection exceeds review inventory bound")
    output = args.output
    with output.open("x") as target:
        _ = target.write(
            json.dumps(
                [row.model_dump(mode="json") for row in proposals],
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    with tarfile.open(archive) as source:
        notice = source.extractfile("omw-pl/LICENSE")
        if notice is None:
            raise ValueError("missing WordNet notice")
        with output.with_suffix(".license.txt").open("xb") as target:
            _ = target.write(notice.read(65536))
    print(
        f"Generated {len(proposals)} proposed candidates; medical review and source validation are required."
    )


if __name__ == "__main__":
    main()
