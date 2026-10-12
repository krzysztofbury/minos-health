import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast, final
from uuid import uuid4

from minos.ingest.watchers.publication.citations import CitationArchive
from minos.ingest.watchers.publication.safety import (
    PublicPublicationError as PublicPublicationError,
)
from minos.ingest.watchers.publication.safety import (
    prepare_public_root as prepare_public_root,
)

_GENERATION_PATTERN = re.compile(
    r"^(?P<revision>[1-9][0-9]*)-(?P<digest>[0-9a-f]{64})$"
)
_BUILDING_PATTERN = re.compile(r"^\.building-[0-9a-f]{32}$")
_DELETING_PATTERN = re.compile(r"^\.deleting-[0-9a-f]{32}$")
_MAX_GENERATIONS = 1024
_MAX_STALE_DIRECTORIES = 128
_MAX_PROJECTION_BYTES = 8 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024
_SOURCES = {"RSK": "rsk.json", "BAW": "baw.json", "NFZ": "nfz.json"}


def _no_publication_fault(_phase: str) -> None:
    pass


@final
class ImmutableGenerationPublisher:
    def __init__(
        self,
        root: Path,
        *,
        source: str = "RSK",
        retained_generations: int = 3,
        retain_citations: bool = False,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        if retained_generations < 1 or retained_generations > 16:
            raise PublicPublicationError("generation retention is unsupported")
        if source not in _SOURCES:
            raise PublicPublicationError("public generation source is unsupported")
        self._root = root
        self._source = source
        self._filename = _SOURCES[source]
        self._projection_path = f"api/v1/provenance/{self._filename}"
        self._tree_layout: dict[str, dict[str, str]] = {
            "": {"manifest.json": "file", "api": "directory"},
            "api": {"v1": "directory"},
            "api/v1": {"provenance": "directory"},
            "api/v1/provenance": {self._filename: "file"},
        }
        self._retained_generations = retained_generations
        self._retain_citations = retain_citations
        self._fault = fault or _no_publication_fault

    def citation_revisions(self) -> frozenset[int] | None:
        if not self._retain_citations:
            return None
        descriptor = self._open_root()
        os.close(descriptor)
        return frozenset(
            record.revision
            for record in CitationArchive(self._root / "citations").records()
        )

    def archive_citation(
        self,
        state_revision: int,
        projection_sha256: str,
        files: Mapping[str, bytes],
    ) -> None:
        self._validate_publication(state_revision, projection_sha256, files)
        if self._retain_citations:
            descriptor = self._open_root()
            os.close(descriptor)
            CitationArchive(self._root / "citations").publish(
                state_revision, projection_sha256, files[self._projection_path]
            )

    def publish(
        self,
        state_revision: int,
        projection_sha256: str,
        files: Mapping[str, bytes],
    ) -> str:
        self._validate_publication(state_revision, projection_sha256, files)
        generation = f"{state_revision}-{projection_sha256}"
        root_descriptor = self._open_root()
        try:
            generations_descriptor = self._open_or_create_directory(
                root_descriptor,
                "generations",
                0o755,
            )
            try:
                self._cleanup_stale(generations_descriptor)
                current = self._read_current(root_descriptor)
                if current is not None:
                    if state_revision < current[0]:
                        raise PublicPublicationError(
                            "public projection downgrade requires an explicit rebuild"
                        )
                    if state_revision == current[0] and current[1] != generation:
                        raise PublicPublicationError(
                            "public revision already has a different projection digest"
                        )
                    if current[1] != generation:
                        self._validate_stored_generation(
                            generations_descriptor,
                            current[1],
                        )
                self.archive_citation(state_revision, projection_sha256, files)
                if current is not None and current[1] == generation:
                    self._install_or_rebuild_dangling(
                        generations_descriptor,
                        generation,
                        files,
                    )
                    self._collect_garbage(generations_descriptor, generation)
                    return generation
                self._install_generation(generations_descriptor, generation, files)
                self._fault("generation_fsynced")
                self._switch_current(root_descriptor, generation)
                self._fault("current_switched")
                self._collect_garbage(generations_descriptor, generation)
                return generation
            finally:
                os.close(generations_descriptor)
        except PublicPublicationError:
            raise
        except OSError:
            raise PublicPublicationError(
                "public generation publication failed"
            ) from None
        finally:
            os.close(root_descriptor)

    def rebuild(
        self,
        *,
        expected_current_revision: int,
        expected_current_sha256: str,
        target_revision: int,
        target_sha256: str,
        files: Mapping[str, bytes],
    ) -> str:
        if type(expected_current_revision) is not int or expected_current_revision < 1:
            raise PublicPublicationError("public rebuild expectation is invalid")
        self._validate_digest(expected_current_sha256)
        self._validate_publication(target_revision, target_sha256, files)
        expected = (
            expected_current_revision,
            f"{expected_current_revision}-{expected_current_sha256}",
        )
        target = f"{target_revision}-{target_sha256}"
        root_descriptor = self._open_root()
        try:
            generations_descriptor = self._open_or_create_directory(
                root_descriptor,
                "generations",
                0o755,
            )
            try:
                self._cleanup_stale(generations_descriptor)
                current = self._read_current(root_descriptor)
                if current != expected:
                    raise PublicPublicationError(
                        "public rebuild current expectation is stale"
                    )
                self._validate_stored_generation(
                    generations_descriptor,
                    expected[1],
                )
                self.archive_citation(target_revision, target_sha256, files)
                self._install_generation(generations_descriptor, target, files)
                self._fault("generation_fsynced")
                self._switch_current(root_descriptor, target)
                self._fault("current_switched")
                self._collect_garbage(generations_descriptor, target)
                return target
            finally:
                os.close(generations_descriptor)
        except PublicPublicationError:
            raise
        except OSError:
            raise PublicPublicationError("public generation rebuild failed") from None
        finally:
            os.close(root_descriptor)

    def current(self) -> tuple[int, str] | None:
        root_descriptor = self._open_root()
        try:
            current = self._read_current(root_descriptor)
            if current is None:
                return None
            generations_descriptor = self._open_directory(
                root_descriptor,
                "generations",
                0o755,
            )
            try:
                self._validate_stored_generation(generations_descriptor, current[1])
            finally:
                os.close(generations_descriptor)
            return current
        except PublicPublicationError:
            raise
        except OSError:
            raise PublicPublicationError(
                "public current generation is invalid"
            ) from None
        finally:
            os.close(root_descriptor)

    def _validate_publication(
        self,
        state_revision: int,
        projection_sha256: str,
        files: Mapping[str, bytes],
    ) -> None:
        if type(state_revision) is not int or state_revision < 1:
            raise PublicPublicationError("public state revision is invalid")
        self._validate_digest(projection_sha256)
        if tuple(sorted(files)) != tuple(
            sorted(("manifest.json", self._projection_path))
        ):
            raise PublicPublicationError("public generation file set is invalid")
        projection = files[self._projection_path]
        manifest = files["manifest.json"]
        if len(projection) < 1 or len(projection) > _MAX_PROJECTION_BYTES:
            raise PublicPublicationError("public projection size is invalid")
        if hashlib.sha256(projection).hexdigest() != projection_sha256:
            raise PublicPublicationError("public projection digest differs")
        self._validate_manifest(
            manifest,
            state_revision,
            projection_sha256,
            len(projection),
        )

    def _validate_digest(self, digest: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise PublicPublicationError("public projection digest is invalid")

    def _validate_manifest(
        self,
        content: bytes,
        state_revision: int,
        projection_sha256: str,
        projection_size_bytes: int,
    ) -> None:
        if len(content) < 2 or len(content) > _MAX_MANIFEST_BYTES:
            raise PublicPublicationError("public manifest size is invalid")
        document = _decode_json_object(content)
        expected: dict[str, object] = {
            "projection_path": self._projection_path,
            "projection_sha256": projection_sha256,
            "projection_size_bytes": projection_size_bytes,
            "redistribution": {"artifacts": [], "schema_version": 1},
            "schema_version": 1,
            "source": self._source,
            "state_revision": state_revision,
        }
        if document != expected or content != _canonical_bytes(document):
            raise PublicPublicationError("public manifest fields are invalid")
        if (
            type(document["schema_version"]) is not int
            or type(document["state_revision"]) is not int
            or type(document["projection_size_bytes"]) is not int
        ):
            raise PublicPublicationError("public manifest numeric fields are invalid")
        redistribution = document["redistribution"]
        typed_redistribution: dict[object, object] = (
            cast(dict[object, object], redistribution)
            if isinstance(redistribution, dict)
            else {}
        )
        if (
            not isinstance(redistribution, dict)
            or type(typed_redistribution.get("schema_version")) is not int
        ):
            raise PublicPublicationError(
                "public manifest redistribution fields are invalid"
            )

    def _open_root(self) -> int:
        descriptor = -1
        try:
            descriptor = os.open(
                self._root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            status = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) & 0o022
            ):
                raise PublicPublicationError("public root ownership or mode is unsafe")
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _open_or_create_directory(
        self,
        parent_descriptor: int,
        name: str,
        mode: int,
    ) -> int:
        created = False
        try:
            os.mkdir(name, mode, dir_fd=parent_descriptor)
            created = True
            os.fsync(parent_descriptor)
        except FileExistsError:
            pass
        descriptor = self._open_directory(
            parent_descriptor, name, mode, created=created
        )
        return descriptor

    def _open_directory(
        self,
        parent_descriptor: int,
        name: str,
        mode: int,
        *,
        created: bool = False,
    ) -> int:
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
            if created:
                os.fchmod(descriptor, mode)
            descriptor_status = os.fstat(descriptor)
            path_status = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(descriptor_status.st_mode)
                or descriptor_status.st_uid != os.geteuid()
                or descriptor_status.st_dev != path_status.st_dev
                or descriptor_status.st_ino != path_status.st_ino
                or stat.S_IMODE(descriptor_status.st_mode) != mode
            ):
                raise PublicPublicationError("public generation directory is unsafe")
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _open_directory_any_mode(self, parent_descriptor: int, name: str) -> int:
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
            status = os.fstat(descriptor)
            path_status = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(status.st_mode)
                or status.st_uid != os.geteuid()
                or status.st_dev != path_status.st_dev
                or status.st_ino != path_status.st_ino
            ):
                raise PublicPublicationError("public generation directory is unsafe")
            os.fchmod(descriptor, 0o755)  # nosec B103
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _install_or_rebuild_dangling(
        self,
        generations_descriptor: int,
        generation: str,
        files: Mapping[str, bytes],
    ) -> None:
        try:
            self._validate_generation(generations_descriptor, generation, files)
        except FileNotFoundError:
            self._install_generation(generations_descriptor, generation, files)

    def _install_generation(
        self,
        generations_descriptor: int,
        generation: str,
        files: Mapping[str, bytes],
    ) -> None:
        try:
            self._validate_generation(generations_descriptor, generation, files)
            return
        except FileNotFoundError:
            pass
        temporary = f".building-{uuid4().hex}"
        temporary_descriptor = -1
        try:
            os.mkdir(temporary, 0o700, dir_fd=generations_descriptor)
            temporary_descriptor = self._open_directory(
                generations_descriptor,
                temporary,
                0o700,
            )
            self._write_generation(temporary_descriptor, files)
            os.fchmod(temporary_descriptor, 0o555)  # nosec B103
            os.fsync(temporary_descriptor)
            os.rename(
                temporary,
                generation,
                src_dir_fd=generations_descriptor,
                dst_dir_fd=generations_descriptor,
            )
            os.fsync(generations_descriptor)
        except FileExistsError:
            self._validate_generation(generations_descriptor, generation, files)
        finally:
            if temporary_descriptor >= 0:
                os.close(temporary_descriptor)
            try:
                self._remove_tree(generations_descriptor, temporary, "stale")
                os.fsync(generations_descriptor)
            except FileNotFoundError, PublicPublicationError, OSError:
                pass

    def _write_generation(
        self,
        generation_descriptor: int,
        files: Mapping[str, bytes],
    ) -> None:
        api_descriptor = self._create_directory(generation_descriptor, "api")
        v1_descriptor = -1
        provenance_descriptor = -1
        try:
            v1_descriptor = self._create_directory(api_descriptor, "v1")
            provenance_descriptor = self._create_directory(v1_descriptor, "provenance")
            self._write_file(
                generation_descriptor,
                "manifest.json",
                files["manifest.json"],
            )
            self._write_file(
                provenance_descriptor,
                self._filename,
                files[self._projection_path],
            )
            for descriptor in (
                provenance_descriptor,
                v1_descriptor,
                api_descriptor,
            ):
                os.fchmod(descriptor, 0o555)  # nosec B103
                os.fsync(descriptor)
        finally:
            if provenance_descriptor >= 0:
                os.close(provenance_descriptor)
            if v1_descriptor >= 0:
                os.close(v1_descriptor)
            os.close(api_descriptor)

    def _create_directory(self, parent_descriptor: int, name: str) -> int:
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        descriptor = self._open_directory(parent_descriptor, name, 0o700)
        try:
            os.fchmod(descriptor, 0o755)  # nosec B103
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _write_file(self, directory_descriptor: int, name: str, content: bytes) -> None:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        try:
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                if written < 1:
                    raise PublicPublicationError("public file write was incomplete")
                offset += written
            os.fchmod(descriptor, 0o444)
            os.fsync(descriptor)
            status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or status.st_nlink != 1
                or status.st_size != len(content)
                or stat.S_IMODE(status.st_mode) != 0o444
            ):
                raise PublicPublicationError("public file identity is unsafe")
        finally:
            os.close(descriptor)

    def _read_current(self, root_descriptor: int) -> tuple[int, str] | None:
        try:
            status = os.stat("current", dir_fd=root_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISLNK(status.st_mode)
            or status.st_uid != os.geteuid()
            or status.st_nlink != 1
        ):
            raise PublicPublicationError("public current pointer is unsafe")
        target = os.readlink("current", dir_fd=root_descriptor)
        prefix = "generations/"
        if not target.startswith(prefix) or "/" in target[len(prefix) :]:
            raise PublicPublicationError("public current target is invalid")
        generation = target[len(prefix) :]
        match = _GENERATION_PATTERN.fullmatch(generation)
        if match is None:
            raise PublicPublicationError("public current generation is invalid")
        return int(match.group("revision")), generation

    def _switch_current(self, root_descriptor: int, generation: str) -> None:
        temporary = f".current-{uuid4().hex}"
        try:
            os.symlink(
                f"generations/{generation}",
                temporary,
                dir_fd=root_descriptor,
            )
            status = os.stat(temporary, dir_fd=root_descriptor, follow_symlinks=False)
            if not stat.S_ISLNK(status.st_mode) or status.st_uid != os.geteuid():
                raise PublicPublicationError("temporary public pointer is unsafe")
            os.replace(
                temporary,
                "current",
                src_dir_fd=root_descriptor,
                dst_dir_fd=root_descriptor,
            )
            os.fsync(root_descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=root_descriptor)
            except FileNotFoundError:
                pass

    def _validate_generation(
        self,
        generations_descriptor: int,
        generation: str,
        files: Mapping[str, bytes],
    ) -> None:
        stored = self._read_generation(generations_descriptor, generation)
        if stored != dict(files):
            raise PublicPublicationError("public generation content differs")

    def _validate_stored_generation(
        self,
        generations_descriptor: int,
        generation: str,
    ) -> None:
        _ = self._read_generation(generations_descriptor, generation)

    def _read_generation(
        self,
        generations_descriptor: int,
        generation: str,
    ) -> dict[str, bytes]:
        match = _GENERATION_PATTERN.fullmatch(generation)
        if match is None:
            raise PublicPublicationError("public generation name is invalid")
        generation_descriptor = self._open_directory(
            generations_descriptor,
            generation,
            0o555,
        )
        try:
            self._require_tree_layout(generation_descriptor, "")
            manifest = self._read_file(
                generation_descriptor,
                "manifest.json",
                _MAX_MANIFEST_BYTES,
            )
            api_descriptor = self._open_directory(generation_descriptor, "api", 0o555)
            try:
                self._require_tree_layout(api_descriptor, "api")
                v1_descriptor = self._open_directory(api_descriptor, "v1", 0o555)
                try:
                    self._require_tree_layout(v1_descriptor, "api/v1")
                    provenance_descriptor = self._open_directory(
                        v1_descriptor,
                        "provenance",
                        0o555,
                    )
                    try:
                        self._require_tree_layout(
                            provenance_descriptor,
                            "api/v1/provenance",
                        )
                        projection = self._read_file(
                            provenance_descriptor,
                            self._filename,
                            _MAX_PROJECTION_BYTES,
                        )
                    finally:
                        os.close(provenance_descriptor)
                finally:
                    os.close(v1_descriptor)
            finally:
                os.close(api_descriptor)
        finally:
            os.close(generation_descriptor)
        revision = int(match.group("revision"))
        digest = match.group("digest")
        if hashlib.sha256(projection).hexdigest() != digest:
            raise PublicPublicationError("stored public projection digest differs")
        self._validate_manifest(manifest, revision, digest, len(projection))
        return {"manifest.json": manifest, self._projection_path: projection}

    def _require_tree_layout(
        self,
        directory_descriptor: int,
        relative_path: str,
    ) -> None:
        if set(os.listdir(directory_descriptor)) != set(
            self._tree_layout[relative_path]
        ):
            raise PublicPublicationError("public generation tree layout differs")

    def _read_file(
        self,
        directory_descriptor: int,
        name: str,
        maximum_size_bytes: int,
    ) -> bytes:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_descriptor,
        )
        try:
            status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or status.st_nlink != 1
                or status.st_size < 1
                or status.st_size > maximum_size_bytes
                or stat.S_IMODE(status.st_mode) != 0o444
            ):
                raise PublicPublicationError("public generation file is unsafe")
            remaining = status.st_size
            blocks: list[bytes] = []
            while remaining > 0:
                block = os.read(descriptor, min(65_536, remaining))
                if not block:
                    raise PublicPublicationError("public generation file is truncated")
                blocks.append(block)
                remaining -= len(block)
            if os.read(descriptor, 1):
                raise PublicPublicationError("public generation file grew while read")
            return b"".join(blocks)
        finally:
            os.close(descriptor)

    def _cleanup_stale(self, generations_descriptor: int) -> None:
        names = os.listdir(generations_descriptor)
        if len(names) > _MAX_GENERATIONS:
            raise PublicPublicationError("public generation count exceeds its bound")
        if any(
            (
                name.startswith(".building-")
                and _BUILDING_PATTERN.fullmatch(name) is None
            )
            or (
                name.startswith(".deleting-")
                and _DELETING_PATTERN.fullmatch(name) is None
            )
            for name in names
        ):
            raise PublicPublicationError("stale public generation name is invalid")
        stale = [
            name
            for name in names
            if _BUILDING_PATTERN.fullmatch(name) is not None
            or _DELETING_PATTERN.fullmatch(name) is not None
        ]
        if len(stale) > _MAX_STALE_DIRECTORIES:
            raise PublicPublicationError(
                "stale public generation count exceeds its bound"
            )
        for name in sorted(stale):
            self._remove_tree(generations_descriptor, name, "stale")
        if stale:
            os.fsync(generations_descriptor)

    def _collect_garbage(self, generations_descriptor: int, current: str) -> None:
        names = os.listdir(generations_descriptor)
        if len(names) > _MAX_GENERATIONS:
            raise PublicPublicationError("public generation count exceeds its bound")
        generations: list[tuple[int, str]] = []
        for name in names:
            match = _GENERATION_PATTERN.fullmatch(name)
            if match is not None:
                generations.append((int(match.group("revision")), name))
        retained = {current}
        for _revision, name in sorted(generations, reverse=True):
            if len(retained) >= self._retained_generations:
                break
            retained.add(name)
        for _revision, name in sorted(generations):
            if name not in retained:
                self._tombstone_generation(generations_descriptor, name)

    def _tombstone_generation(
        self,
        generations_descriptor: int,
        generation: str,
    ) -> None:
        tombstone = f".deleting-{uuid4().hex}"
        os.rename(
            generation,
            tombstone,
            src_dir_fd=generations_descriptor,
            dst_dir_fd=generations_descriptor,
        )
        self._fault("gc_victim_renamed")
        os.fsync(generations_descriptor)
        self._fault("gc_rename_fsynced")
        self._remove_tree(generations_descriptor, tombstone, "gc")
        os.fsync(generations_descriptor)
        self._fault("gc_delete_fsynced")

    def _remove_tree(
        self,
        parent_descriptor: int,
        name: str,
        fault_prefix: str,
        relative_path: str = "",
    ) -> None:
        descriptor = self._open_directory_any_mode(parent_descriptor, name)
        try:
            entries = set(os.listdir(descriptor))
            allowed = self._tree_layout[relative_path]
            if not entries.issubset(allowed):
                raise PublicPublicationError(
                    "refusing to remove an unexpected public generation"
                )
            for entry in sorted(entries):
                entry_type = allowed[entry]
                child_path = f"{relative_path}/{entry}".strip("/")
                if entry_type == "directory":
                    self._remove_tree(
                        descriptor,
                        entry,
                        fault_prefix,
                        child_path,
                    )
                else:
                    self._unlink_tree_file(descriptor, entry)
                    self._fault(f"{fault_prefix}_file_unlinked")
        finally:
            os.close(descriptor)
        os.rmdir(name, dir_fd=parent_descriptor)
        self._fault(f"{fault_prefix}_directory_removed")

    def _unlink_tree_file(self, directory_descriptor: int, name: str) -> None:
        status = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or status.st_nlink != 1
        ):
            raise PublicPublicationError("public deletion file is unsafe")
        os.unlink(name, dir_fd=directory_descriptor)


def _decode_json_object(content: bytes) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document: dict[str, object] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("duplicate JSON member")
            document[key] = value
        return document

    try:
        value = cast(object, json.loads(content, object_pairs_hook=unique_object))
    except UnicodeDecodeError, ValueError, TypeError:
        raise PublicPublicationError("public manifest JSON is invalid") from None
    if not isinstance(value, dict):
        raise PublicPublicationError("public manifest is not an object")
    untyped = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in untyped):
        raise PublicPublicationError("public manifest key is invalid")
    return cast(dict[str, object], value)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
