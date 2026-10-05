# Docker inspection snapshots are independently checked, not production validators.
# pyright: reportAny=false

"""Exercise the data-only OCI overlay in Docker using a synthetic embedded bundle.

Run under agent-scratch with --work pointing inside its owned directory. The
base image must already exist; this command never pulls, publishes or signs.
"""

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path
from typing import final

from minos.ingest.processing.data_image import (
    DataImagePins,
    package_data_image,
    verify_data_image,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.smoke_reference_web import fixture  # noqa: E402

PROBE = """
import json,os,stat,time,urllib.request
from pathlib import Path
for _ in range(100):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8081/readyz', timeout=2) as response:
            assert response.status == 200
        break
    except OSError:
        time.sleep(0.1)
else:
    raise RuntimeError('embedded API did not become ready')
assert os.getuid() == 10004
for name in ('manifest.json', 'minos.db', 'review.txt'):
    path=Path('/app/data')/name
    info=path.lstat()
    assert stat.S_ISREG(info.st_mode)
    assert (info.st_mode & 0o777, info.st_uid, info.st_gid) == (0o444,10004,10004)
with urllib.request.urlopen('http://127.0.0.1:8081/api/v1/releases', timeout=5) as response:
    metadata=json.load(response)
source=next(item for item in metadata['releases'] if item['system']=='icd9-pl' and item['origin']=='czs')
request=urllib.request.Request('http://127.0.0.1:8081/api/v1/search',
    data=json.dumps({'system':'icd9-pl','release_id':source['release_id'],'query':'badania moczu'}).encode(),
    headers={'Content-Type':'application/json'},method='POST')
with urllib.request.urlopen(request, timeout=5) as response:
    result=json.load(response)
assert result['results']
print(json.dumps({'ready':True,'search':True,'embedded_files_read_only':True}))
"""


def docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True, timeout=180
    ).stdout.strip()


def blob(archive: tarfile.TarFile, descriptor: dict[str, object]) -> bytes:
    path = "blobs/sha256/" + str(descriptor["digest"]).removeprefix("sha256:")
    source = archive.extractfile(path)
    assert source is not None
    with source:
        content = source.read(1024 * 1024 + 1)
    assert len(content) <= 1024 * 1024
    assert len(content) == descriptor["size"]
    assert "sha256:" + hashlib.sha256(content).hexdigest() == descriptor["digest"]
    return content


def add_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    header = tarfile.TarInfo(name)
    header.size = len(content)
    archive.addfile(header, io.BytesIO(content))


@final
class Arguments(argparse.Namespace):
    base_image: str = ""
    work: Path = Path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--base-image", required=True)
    _ = parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args(namespace=Arguments())
    work: Path = args.work
    work.mkdir(exist_ok=False)
    bundle = fixture(work)
    base_id = docker("image", "inspect", "--format", "{{.Id}}", args.base_image)
    base_probe = """
from pathlib import Path
for path in (Path('/app'),Path('/app/data')):
    assert not path.is_symlink()
    assert not path.exists() or path.is_dir()
assert not Path('/app/data').exists() or not list(Path('/app/data').iterdir())
"""
    _ = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--entrypoint",
        "/build/.venv/bin/python",
        base_id,
        "-c",
        base_probe,
    )
    _ = docker("image", "save", base_id, "--output", str(work / "base.tar"))
    tag = "minos-data-overlay-smoke:" + uuid.uuid4().hex
    container = "minos-data-overlay-smoke-" + uuid.uuid4().hex
    loaded = False
    started = False
    try:
        with tarfile.open(work / "base.tar") as base:
            stream = base.extractfile("index.json")
            assert stream is not None
            index = json.load(stream)
            descriptor = index["manifests"][0]
            for _ in range(4):
                manifest_bytes = blob(base, descriptor)
                manifest = json.loads(manifest_bytes)
                if "layers" in manifest:
                    break
                descriptor = next(
                    item
                    for item in manifest["manifests"]
                    if item.get("platform", {}).get("architecture") == "amd64"
                )
            else:
                raise RuntimeError("base runnable manifest is unavailable")
            config_bytes = blob(base, manifest["config"])
            pins = DataImagePins(
                application_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                artifact_manifest_sha256=hashlib.sha256(
                    (bundle / "manifest.json").read_bytes()
                ).hexdigest(),
            )
            output = work / "delta"
            result = package_data_image(
                application_manifest=manifest_bytes,
                application_config=config_bytes,
                bundle=bundle,
                pins=pins,
                output=output,
            )
            assert (
                verify_data_image(
                    application_manifest=manifest_bytes,
                    application_config=config_bytes,
                    image_manifest=(output / "image-manifest.json").read_bytes(),
                    image_config=(output / "image-config.json").read_bytes(),
                    data_layer=output / "data-layer.tar",
                    bundle=bundle,
                    pins=pins,
                )
                == result
            )
            with tarfile.open(work / "candidate.tar", "w") as candidate:
                # Copy original compressed application blobs without conversion.
                for layer in manifest["layers"]:
                    name = "blobs/sha256/" + layer["digest"].removeprefix("sha256:")
                    header = base.getmember(name)
                    assert header.size == layer["size"]
                    source = base.extractfile(header)
                    assert source is not None
                    with source:
                        candidate.addfile(header, source)
                for filename, digest in (
                    ("image-manifest.json", result.image_manifest_sha256),
                    ("image-config.json", result.image_config_sha256),
                    ("data-layer.tar", result.data_layer_sha256),
                ):
                    candidate.add(output / filename, arcname="blobs/sha256/" + digest)
                add_bytes(candidate, "oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
                add_bytes(
                    candidate,
                    "index.json",
                    json.dumps(
                        {
                            "schemaVersion": 2,
                            "mediaType": "application/vnd.oci.image.index.v1+json",
                            "manifests": [
                                {
                                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                                    "digest": "sha256:" + result.image_manifest_sha256,
                                    "size": (output / "image-manifest.json")
                                    .stat()
                                    .st_size,
                                    "annotations": {
                                        "io.containerd.image.name": "docker.io/library/"
                                        + tag,
                                        "org.opencontainers.image.ref.name": tag,
                                    },
                                }
                            ],
                        }
                    ).encode(),
                )
        loaded = True
        _ = docker("image", "load", "--input", str(work / "candidate.tar"))
        candidate_info = json.loads(docker("image", "inspect", tag))[0]
        assert (
            candidate_info["Descriptor"]["digest"]
            == "sha256:" + result.image_manifest_sha256
        )
        # Containerd-backed Docker IDs are manifest identities, not config hashes.
        # Check the runnable descriptor and actual inherited runtime separately.
        base_info = json.loads(docker("image", "inspect", base_id))[0]
        assert candidate_info["Config"] == base_info["Config"]
        assert (
            candidate_info["RootFS"]["Layers"]
            == json.loads((output / "image-config.json").read_bytes())["rootfs"][
                "diff_ids"
            ]
        )
        started = True
        _ = docker(
            "run",
            "--detach",
            "--name",
            container,
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--env",
            "MINOS_REFERENCE_DATABASE_SHA256=" + result.database_sha256,
            tag,
        )
        settings = json.loads(docker("inspect", container))[0]
        assert settings["Mounts"] == []
        assert settings["Image"] == candidate_info["Id"]
        runtime = json.loads(
            docker("exec", container, "/build/.venv/bin/python", "-c", PROBE)
        )
        print(
            json.dumps(
                {
                    **result.model_dump(),
                    "docker_manifest_verified": True,
                    "no_data_mount": True,
                    **runtime,
                },
                sort_keys=True,
            )
        )
    finally:
        if started:
            _ = docker("container", "rm", "--force", container)
        if loaded:
            _ = docker("image", "rm", tag)


if __name__ == "__main__":
    main()
