import hashlib
import json
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.http.app import create_app
from minos.http.source_reader import SourceUnavailable, load_snapshot
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.watchers.publication import (
    ImmutableGenerationPublisher,
    prepare_public_root,
)
from tests.integration.test_canonical_reference import canonical_manifest
from tests.unit.test_sources_page import (
    _baw_projection,  # pyright: ignore[reportPrivateUsage]
    _projection,  # pyright: ignore[reportPrivateUsage]
)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("source-pages-bundle")
    output = root / "bundle"
    _ = build_reference_artifact(canonical_manifest(root), root, output)
    return output


def publish(root: Path, source: str, document: dict[str, object]) -> str:
    prepare_public_root(root)
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    revision = cast(int, document["through_state_revision"])
    projection_path = f"api/v1/provenance/{source.lower()}.json"
    manifest = json.dumps(
        {
            "schema_version": 1,
            "source": source,
            "state_revision": revision,
            "projection_path": projection_path,
            "projection_sha256": digest,
            "projection_size_bytes": len(payload),
            "redistribution": {"schema_version": 1, "artifacts": []},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return ImmutableGenerationPublisher(
        root, source=source, retained_generations=1, retain_citations=source != "NFZ"
    ).publish(revision, digest, {"manifest.json": manifest, projection_path: payload})


def rsk() -> dict[str, object]:
    document = _projection()
    document["entries"] = sorted(
        cast(list[dict[str, object]], document["entries"]),
        key=lambda entry: str(entry["last_observed_at"]),
        reverse=True,
    )
    return document


def test_initial_html_and_citation_survive_new_publications(
    bundle: Path, tmp_path: Path
) -> None:
    identity = publish(tmp_path, "RSK", rsk())
    _ = publish(tmp_path / "baw", "BAW", _baw_projection())
    with TestClient(create_app(bundle, public_directory=tmp_path)) as client:
        initial = client.get("/zrodla")
        assert initial.status_code == 200
        assert 'href="/zrodla/rsk/00CD10"' in initial.text
        assert 'href="/zrodla/baw/43868"' in initial.text
        assert 'data-fallback-source="nfz"' in initial.text
        assert "Dane tego źródła są niedostępne" in initial.text
        assert "<img src=x" not in initial.text
        assert "&lt;img src=x" in initial.text
        dataset = client.get("/zrodla/rsk/00CD10")
        path = f"/zrodla/rsk/obserwacje/{identity}/00CD10"
        assert f'href="{path}"' in dataset.text
        assert "Ważność od wg RSK" in dataset.text
        assert "Pierwsza obserwacja Minos (UTC)" in dataset.text
        cited = client.get(path)
        assert cited.status_code == 200
        assert f'href="https://minoshealth.pl{path}"' in cited.text
        assert "obserwacja historyczna" in cited.text
        updated = rsk()
        updated.update(through_state_revision=14, included_revisions=[10, 11, 12, 14])
        for row in cast(list[dict[str, object]], updated["entries"]):
            row["resource_version"] = "NEW-VERSION"
        _ = publish(tmp_path, "RSK", updated)
        assert "NEW-VERSION" in client.get("/zrodla/rsk/00CD10").text
        assert client.get(path).text == cited.text
        assert len(list((tmp_path / "generations").iterdir())) == 1
        assert client.get("/zrodla/baw/43868").status_code == 200
        assert 'href="/zrodla/baw/43943"' in client.get("/zrodla/baw/43868").text
        assert client.get("/zrodla/rsk/00CD10?q=private-canary").status_code == 400
        assert client.get("/zrodla/rsk/UNKNOWN").status_code == 404
        assert client.get("/zrodla/rsk/obserwacje/123-" + "a" * 64).status_code == 404
        assert client.get("/zrodla/rsk/00CD10/strona/999").status_code == 404


@pytest.mark.parametrize(
    "mutation", ["digest", "symlink", "private-field", "official-url"]
)
def test_bad_provider_cannot_hide_good_source(
    bundle: Path, tmp_path: Path, mutation: str
) -> None:
    _ = publish(tmp_path, "RSK", rsk())
    document = _baw_projection()
    if mutation == "private-field":
        document["private-canary"] = "do-not-publish"
    elif mutation == "official-url":
        cast(list[dict[str, object]], document["documents"])[0]["source_url"] = (
            "https://private.invalid/secret"
        )
    _ = publish(tmp_path / "baw", "BAW", document)
    path = tmp_path / "baw/current/api/v1/provenance/baw.json"
    if mutation == "digest":
        path.chmod(0o644)
        _ = path.write_bytes(b"private-canary")
    elif mutation == "symlink":
        path.parent.chmod(0o755)
        path.unlink()
        path.symlink_to(tmp_path / "current/api/v1/provenance/rsk.json")
    with TestClient(create_app(bundle, public_directory=tmp_path)) as client:
        response = client.get("/zrodla")
        assert response.status_code == 200
        assert 'href="/zrodla/rsk/00CD10"' in response.text
        assert "Dane tego źródła są niedostępne" in response.text
        assert "private-canary" not in response.text
        assert "private.invalid" not in response.text
        assert client.get("/zrodla/baw").status_code == 503


def test_citation_reader_refuses_hash_and_revision_substitution(tmp_path: Path) -> None:
    identity = publish(tmp_path, "RSK", rsk())
    assert load_snapshot(tmp_path, "rsk", identity).retained
    with pytest.raises(SourceUnavailable):
        _ = load_snapshot(tmp_path, "rsk", "../control.sqlite3")
    path = tmp_path / "citations" / (identity + ".json")
    data = cast(dict[str, object], json.loads(path.read_bytes()))
    data["through_state_revision"] = 99
    data["included_revisions"] = [99]
    payload = json.dumps(data).encode()
    digest = hashlib.sha256(payload).hexdigest()
    alternate = tmp_path / "citations" / f"12-{digest}.json"
    _ = alternate.write_bytes(payload)
    with pytest.raises(SourceUnavailable, match="revision differs"):
        _ = load_snapshot(tmp_path, "rsk", f"12-{digest}")


def test_nojs_pagination_is_complete_and_keeps_the_observation_fence(
    bundle: Path, tmp_path: Path
) -> None:
    document = rsk()
    row = cast(list[dict[str, object]], document["entries"])[0]
    document["entries"] = [
        {**row, "hcd": f"00X{index:03}", "resource_name": f"Dataset {index:03}"}
        for index in range(25)
    ]
    identity = publish(tmp_path, "RSK", document)
    with TestClient(create_app(bundle, public_directory=tmp_path)) as client:
        base = "/zrodla/rsk/obserwacje/" + identity
        first = client.get(base)
        second = client.get(base + "/strona/2")
        assert first.status_code == second.status_code == 200
        assert first.text.count('<article class="source-card">') == 20
        assert second.text.count('<article class="source-card">') == 5
        assert 'href="' + base + '/strona/2"' in first.text
        assert 'href="' + base + '/00X024"' in second.text
        assert "<h3>Dataset 024</h3>" not in first.text
        assert client.get(base + "/strona/3").status_code == 404
        assert client.get(base + "/strona/1").status_code == 404
