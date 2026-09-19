import fcntl
import hashlib
import io
import os
import stat
from collections.abc import Buffer, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, cast, override

import pytest

from minos.ingest.watchers.archive import (
    BlobArchiveStorageError,
    BlobArchiveVerificationError,
    BlobStagingArea,
    FilesystemBlobArchive,
)
from minos.ingest.watchers.archive import filesystem as filesystem_module
from minos.ingest.watchers.archive.filesystem import (
    _copy_exact,  # pyright: ignore[reportPrivateUsage]
)


def test_filesystem_archive_creates_and_verifies_digest_path(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    archive = FilesystemBlobArchive(tmp_path / "archive")

    with staging.stage() as writer:
        _ = writer.write(b"raw source bytes")
        blob = writer.seal(media_type="application/xml")
        receipt = archive.put(blob)

    destination = tmp_path / "archive" / receipt.archive_key
    assert destination.read_bytes() == b"raw source bytes"
    assert receipt.sha256 == hashlib.sha256(b"raw source bytes").hexdigest()
    assert receipt.verified_sha256 == receipt.sha256
    assert receipt.size_bytes == 16
    assert receipt.verified_size_bytes == 16
    assert receipt.media_type == "application/xml"


def test_filesystem_archive_reuses_existing_verified_blob(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    archive = FilesystemBlobArchive(tmp_path / "archive")

    with staging.stage() as first_writer:
        _ = first_writer.write(b"same")
        first_receipt = archive.put(first_writer.seal(media_type=None))
    with staging.stage() as second_writer:
        _ = second_writer.write(b"same")
        second_receipt = archive.put(second_writer.seal(media_type=None))

    assert second_receipt == first_receipt
    assert len(list((tmp_path / "archive" / "blobs" / "sha256").iterdir())) == 1


def test_filesystem_archive_rejects_corrupt_existing_digest_path(
    tmp_path: Path,
) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)

    with staging.stage() as writer:
        _ = writer.write(b"good")
        blob = writer.seal(media_type=None)
        destination = archive_root / "blobs" / "sha256" / blob.sha256
        _ = destination.write_bytes(b"evil")
        os.chmod(destination, 0o600)

        with pytest.raises(BlobArchiveVerificationError, match="does not match"):
            _ = archive.put(blob)

    assert destination.read_bytes() == b"evil"


def test_filesystem_archive_rejects_atomic_publication_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    archive = FilesystemBlobArchive(tmp_path / "archive")

    def failed_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected link failure")

    monkeypatch.setattr(os, "link", failed_link)
    with staging.stage() as writer:
        _ = writer.write(b"bytes")
        blob = writer.seal(media_type=None)
        with pytest.raises(BlobArchiveStorageError, match="publication failed"):
            _ = archive.put(blob)


def test_filesystem_archive_revalidates_staged_bytes(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    archive = FilesystemBlobArchive(tmp_path / "archive")

    with staging.stage() as writer:
        _ = writer.write(b"original")
        blob = writer.seal(media_type=None)
        _ = blob.path.write_bytes(b"tampered")

        with pytest.raises(BlobArchiveVerificationError, match="declared identity"):
            _ = archive.put(blob)


def test_filesystem_archive_rejects_external_staging_hard_link(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    archive = FilesystemBlobArchive(tmp_path / "archive")
    external = tmp_path / "external-link"

    with staging.stage() as writer:
        _ = writer.write(b"linked")
        blob = writer.seal(media_type=None)
        os.link(blob.path, external)
        try:
            with pytest.raises(BlobArchiveVerificationError, match="hard-link count"):
                _ = archive.put(blob)
        finally:
            external.unlink()


def test_filesystem_archive_does_not_share_staging_inode(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)

    with staging.stage() as writer:
        _ = writer.write(b"immutable")
        blob = writer.seal(media_type=None)
        receipt = archive.put(blob)
        destination = archive_root / receipt.archive_key
        assert blob.path.stat().st_ino != destination.stat().st_ino

        retained_descriptor = os.open(blob.path, os.O_WRONLY)
        try:
            _ = os.write(retained_descriptor, b"corrupted")
        finally:
            os.close(retained_descriptor)

        assert destination.read_bytes() == b"immutable"


def test_filesystem_archive_removes_safe_stale_temporary_file(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)
    stale = archive_root / "temporary" / ".minos-archive-stale"
    _ = stale.write_bytes(b"partial")
    os.chmod(stale, 0o600)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"fresh")
        _ = archive.put(writer.seal(media_type=None))

    assert not stale.exists()


def test_filesystem_archive_rejects_concurrent_publication(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)
    temporary_directory = archive_root / "temporary"
    lock_descriptor = os.open(temporary_directory, os.O_RDONLY)
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with BlobStagingArea(tmp_path / "staging").stage() as writer:
            _ = writer.write(b"locked")
            with pytest.raises(BlobArchiveStorageError, match="opened or locked"):
                _ = archive.put(writer.seal(media_type=None))
    finally:
        os.close(lock_descriptor)


@pytest.mark.parametrize("relative_directory", ["temporary", "blobs/sha256"])
def test_filesystem_archive_revalidates_directory_permissions(
    tmp_path: Path,
    relative_directory: str,
) -> None:
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)
    os.chmod(archive_root / relative_directory, 0o755)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"unsafe directory")
        with pytest.raises(BlobArchiveStorageError, match="group or other"):
            _ = archive.put(writer.seal(media_type=None))


def test_filesystem_archive_preserves_publication_error_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = FilesystemBlobArchive(tmp_path / "archive")
    original_unlink = os.unlink

    def failed_link(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected link failure")

    def selective_failed_unlink(path: str, *, dir_fd: int | None = None) -> None:
        if str(path).startswith(".minos-archive-"):
            raise OSError("injected unlink failure")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "link", failed_link)
    monkeypatch.setattr(os, "unlink", selective_failed_unlink)
    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"bytes")
        with pytest.raises(
            BlobArchiveStorageError, match="publication failed"
        ) as caught:
            _ = archive.put(writer.seal(media_type=None))

    notes = cast(tuple[str, ...], getattr(caught.value, "__notes__", ()))
    assert any("cleanup also failed" in note for note in notes)


def test_filesystem_archive_bounds_copy_if_staged_file_grows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)

    @contextmanager
    def growing_source(_blob: object) -> Generator[BinaryIO]:
        yield io.BytesIO(b"bounded-extra")

    monkeypatch.setattr(filesystem_module, "open_verified_staged_blob", growing_source)
    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"bounded")
        blob = writer.seal(media_type=None)
        with pytest.raises(BlobArchiveVerificationError, match="grew"):
            _ = archive.put(blob)

    assert not (archive_root / "blobs" / "sha256" / blob.sha256).exists()


def test_filesystem_archive_rejects_staged_file_truncation_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)

    @contextmanager
    def truncated_source(_blob: object) -> Generator[BinaryIO]:
        yield io.BytesIO(b"short")

    monkeypatch.setattr(
        filesystem_module,
        "open_verified_staged_blob",
        truncated_source,
    )
    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"complete")
        blob = writer.seal(media_type=None)
        with pytest.raises(BlobArchiveVerificationError, match="shrank"):
            _ = archive.put(blob)

    assert not (archive_root / "blobs" / "sha256" / blob.sha256).exists()


def test_filesystem_archive_rejects_short_destination_write() -> None:
    class ShortWriter(io.BytesIO):
        @override
        def write(self, buffer: Buffer, /) -> int:
            view = memoryview(buffer)
            return super().write(view[:-1])

    with pytest.raises(BlobArchiveStorageError, match="write was incomplete"):
        _copy_exact(io.BytesIO(b"bytes"), ShortWriter(), 5)


def test_filesystem_archive_rejects_hard_linked_stale_file(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)
    stale = archive_root / "temporary" / ".minos-archive-stale"
    external = tmp_path / "stale-link"
    _ = stale.write_bytes(b"partial")
    os.chmod(stale, 0o600)
    os.link(stale, external)

    try:
        with BlobStagingArea(tmp_path / "staging").stage() as writer:
            _ = writer.write(b"fresh")
            with pytest.raises(BlobArchiveStorageError, match="unsafe stale file"):
                _ = archive.put(writer.seal(media_type=None))
    finally:
        stale.unlink()
        external.unlink()


def test_filesystem_archive_bounds_stale_directory_scan(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)
    for index in range(17):
        stale = archive_root / "temporary" / f".minos-archive-{index:02d}"
        _ = stale.write_bytes(b"partial")
        os.chmod(stale, 0o600)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"fresh")
        with pytest.raises(BlobArchiveStorageError, match="exceeds its bound"):
            _ = archive.put(writer.seal(media_type=None))


def test_filesystem_archive_syncs_new_directory_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synchronized_directories: set[tuple[int, int]] = set()
    original_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        status = os.fstat(descriptor)
        if stat.S_ISDIR(status.st_mode):
            synchronized_directories.add((status.st_dev, status.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    archive_root = tmp_path / "archive"
    _ = FilesystemBlobArchive(archive_root)

    expected = {
        (path.stat().st_dev, path.stat().st_ino)
        for path in (tmp_path, archive_root, archive_root / "blobs")
    }
    assert expected <= synchronized_directories


def test_filesystem_archive_rejects_directory_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_fsync(_descriptor: int) -> None:
        raise OSError("injected directory sync failure")

    monkeypatch.setattr(os, "fsync", failed_fsync)
    with pytest.raises(BlobArchiveStorageError, match="could not be synchronized"):
        _ = FilesystemBlobArchive(tmp_path / "archive")


def test_filesystem_archive_resynchronizes_existing_directory_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_root = tmp_path / "archive"

    def failed_fsync(_descriptor: int) -> None:
        raise OSError("injected directory sync failure")

    monkeypatch.setattr(os, "fsync", failed_fsync)
    with pytest.raises(BlobArchiveStorageError, match="could not be synchronized"):
        _ = FilesystemBlobArchive(archive_root)

    monkeypatch.undo()
    _ = FilesystemBlobArchive(archive_root)


def test_filesystem_archive_resynchronizes_reused_link_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_root = tmp_path / "archive"
    archive = FilesystemBlobArchive(archive_root)
    blob_directory = archive_root / "blobs" / "sha256"
    blob_directory_identity = (
        blob_directory.stat().st_dev,
        blob_directory.stat().st_ino,
    )
    original_fsync = os.fsync
    failed_once = False

    def fail_first_blob_directory_sync(descriptor: int) -> None:
        nonlocal failed_once
        status = os.fstat(descriptor)
        if (
            not failed_once
            and (status.st_dev, status.st_ino) == blob_directory_identity
        ):
            failed_once = True
            raise OSError("injected blob directory sync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_first_blob_directory_sync)
    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"durable retry")
        blob = writer.seal(media_type=None)
        with pytest.raises(BlobArchiveStorageError, match="publication failed"):
            _ = archive.put(blob)

        receipt = archive.put(blob)

    assert failed_once
    assert (archive_root / receipt.archive_key).read_bytes() == b"durable retry"
