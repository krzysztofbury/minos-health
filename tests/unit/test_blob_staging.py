import hashlib
import io
import os
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from minos.ingest.watchers.archive import (
    BlobArchiveStorageError,
    BlobArchiveVerificationError,
    BlobSizeLimitError,
    BlobStagingArea,
    BlobStagingError,
    StagedBlobWriter,
)
from minos.ingest.watchers.archive.files import open_verified_staged_blob


def test_staging_hashes_seals_and_removes_mode_600_file(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging", maximum_size_bytes=32)
    staged_path: Path | None = None

    with staging.stage() as writer:
        staged_path = writer.path
        assert writer.write(b"exact ") == 6
        assert writer.write(b"bytes") == 5
        blob = writer.seal(
            media_type="application/octet-stream",
            expected_sha256=hashlib.sha256(b"exact bytes").hexdigest(),
            expected_size_bytes=11,
        )

        assert blob.path == staged_path
        assert blob.size_bytes == 11
        assert blob.sha256 == hashlib.sha256(b"exact bytes").hexdigest()
        assert blob.media_type == "application/octet-stream"
        assert staged_path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(BlobStagingError, match="sealed"):
            _ = writer.write(b"later")

    assert staged_path is not None
    assert not staged_path.exists()


def test_staging_reset_replaces_partial_attempt_identity(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging", maximum_size_bytes=32)

    with staging.stage() as writer:
        _ = writer.write(b"partial")
        assert writer.seek(0) == 0
        assert writer.truncate() == 0
        _ = writer.write(b"complete")
        blob = writer.seal(media_type=None)

        assert blob.sha256 == hashlib.sha256(b"complete").hexdigest()
        assert blob.size_bytes == 8


def test_staging_limit_rejects_write_without_partial_extension(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging", maximum_size_bytes=4)

    with staging.stage() as writer:
        _ = writer.write(b"12")
        with pytest.raises(BlobSizeLimitError, match="byte ceiling"):
            _ = writer.write(b"345")

        assert writer.size_bytes == 2
        assert writer.path.read_bytes() == b"12"


def test_staging_rejects_producer_mismatch_and_cleans_up(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    staged_path: Path | None = None

    with pytest.raises(BlobStagingError, match="checksum"):
        with staging.stage() as writer:
            staged_path = writer.path
            _ = writer.write(b"bytes")
            _ = writer.seal(media_type=None, expected_sha256="0" * 64)

    assert staged_path is not None
    assert not staged_path.exists()


def test_staging_requires_paired_strict_producer_identity(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging")

    with staging.stage() as writer:
        _ = writer.write(b"x")
        with pytest.raises(BlobStagingError, match="provided together"):
            _ = writer.seal(
                media_type=None, expected_sha256=hashlib.sha256(b"x").hexdigest()
            )
        with pytest.raises(BlobStagingError, match="provided together"):
            _ = writer.seal(media_type=None, expected_size_bytes=1)
        with pytest.raises(BlobStagingError, match="outside the supported range"):
            _ = writer.seal(
                media_type=None,
                expected_sha256=hashlib.sha256(b"x").hexdigest(),
                expected_size_bytes=True,
            )


def test_staging_cleans_up_after_base_exception(tmp_path: Path) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    staged_path: Path | None = None

    with pytest.raises(KeyboardInterrupt):
        with staging.stage() as writer:
            staged_path = writer.path
            _ = writer.write(b"partial")
            raise KeyboardInterrupt

    assert staged_path is not None
    assert not staged_path.exists()


def test_staging_preserves_primary_exception_when_close_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = BlobStagingArea(tmp_path / "staging")
    original_close = StagedBlobWriter.close

    def close_then_fail(writer: StagedBlobWriter) -> None:
        original_close(writer)
        raise OSError("injected close failure")

    monkeypatch.setattr(StagedBlobWriter, "close", close_then_fail)
    with pytest.raises(KeyboardInterrupt) as captured:
        with staging.stage() as writer:
            _ = writer.write(b"partial")
            raise KeyboardInterrupt

    notes = cast(tuple[str, ...], getattr(captured.value, "__notes__", ()))
    assert any("cleanup also failed" in note for note in notes)
    assert list((tmp_path / "staging").iterdir()) == []


def test_staging_preserves_base_exception_during_file_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = BlobStagingArea(tmp_path / "staging")

    def interrupt_file(*_args: object, **_kwargs: object) -> io.FileIO:
        raise KeyboardInterrupt

    monkeypatch.setattr(io, "FileIO", interrupt_file)
    with pytest.raises(KeyboardInterrupt):
        with staging.stage():
            raise AssertionError(
                "staging must not yield after interrupted construction"
            )

    assert list((tmp_path / "staging").iterdir()) == []


def test_staging_allows_only_one_active_file_per_directory(tmp_path: Path) -> None:
    directory = tmp_path / "staging"
    first = BlobStagingArea(directory)
    second = BlobStagingArea(directory)

    with first.stage():
        with pytest.raises(BlobStagingError, match="already has an active file"):
            with second.stage():
                raise AssertionError("a second staging file must not be created")

    assert list(directory.iterdir()) == []


def test_staging_removes_safe_stale_file_before_create(tmp_path: Path) -> None:
    directory = tmp_path / "staging"
    staging = BlobStagingArea(directory)
    stale = directory / ".minos-blob-stale"
    _ = stale.write_bytes(b"partial")
    os.chmod(stale, 0o600)

    with staging.stage() as writer:
        assert not stale.exists()
        _ = writer.write(b"new")

    assert list(directory.iterdir()) == []


def test_staging_removes_safe_stale_file_without_creating_a_download(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "staging"
    staging = BlobStagingArea(directory)
    stale = directory / ".minos-blob-stale"
    _ = stale.write_bytes(b"partial")
    os.chmod(stale, 0o600)

    staging.cleanup_stale()

    assert list(directory.iterdir()) == []


def test_staging_rejects_replaced_directory_identity(tmp_path: Path) -> None:
    directory = tmp_path / "staging"
    staging = BlobStagingArea(directory)
    original = tmp_path / "original-staging"
    _ = directory.rename(original)
    directory.mkdir(mode=0o700)

    with pytest.raises(BlobStagingError, match="identity changed"):
        with staging.stage():
            raise AssertionError("replaced staging directory must not be used")

    assert list(directory.iterdir()) == []
    assert list(original.iterdir()) == []


def test_staging_rejects_insecure_directory_permissions(tmp_path: Path) -> None:
    directory = tmp_path / "staging"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)

    with pytest.raises(BlobStagingError, match="group or other"):
        _ = BlobStagingArea(directory)


def test_staged_blob_readers_have_independent_offsets(tmp_path: Path) -> None:
    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"independent")
        blob = writer.seal(media_type=None)

        with open_verified_staged_blob(blob) as first:
            assert first.read(3) == b"ind"
            with open_verified_staged_blob(blob) as second:
                assert second.read() == b"independent"
            assert first.read() == b"ependent"


def test_staged_blob_rejects_use_after_context_exit(tmp_path: Path) -> None:
    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"leased")
        blob = writer.seal(media_type=None)

    with pytest.raises(BlobArchiveStorageError, match="lease is closed"):
        with open_verified_staged_blob(blob):
            raise AssertionError("a closed staged blob lease must not be readable")


def test_staging_revalidates_directory_permissions_before_each_file(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "staging"
    staging = BlobStagingArea(directory)
    os.chmod(directory, 0o755)

    with pytest.raises(BlobStagingError, match="group or other"):
        with staging.stage():
            raise AssertionError("an insecure staging directory must not be used")


def test_staged_blob_rejects_non_integer_runtime_size(tmp_path: Path) -> None:
    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"x")
        blob = writer.seal(media_type=None)
        malformed = replace(blob, size_bytes=cast(int, cast(object, 1.0)))

        with pytest.raises(BlobArchiveVerificationError, match="byte count"):
            with open_verified_staged_blob(malformed):
                raise AssertionError("a non-integer staged size must not be accepted")


def test_staging_rejects_hard_linked_stale_file(tmp_path: Path) -> None:
    directory = tmp_path / "staging"
    staging = BlobStagingArea(directory)
    stale = directory / ".minos-blob-stale"
    external = tmp_path / "stale-link"
    _ = stale.write_bytes(b"partial")
    os.chmod(stale, 0o600)
    os.link(stale, external)

    try:
        with pytest.raises(BlobStagingError, match="unsafe stale file"):
            with staging.stage():
                raise AssertionError("a hard-linked stale file must not be removed")
    finally:
        stale.unlink()
        external.unlink()


def test_staging_bounds_stale_directory_scan(tmp_path: Path) -> None:
    directory = tmp_path / "staging"
    staging = BlobStagingArea(directory)
    for index in range(17):
        stale = directory / f".minos-blob-{index:02d}"
        _ = stale.write_bytes(b"partial")
        os.chmod(stale, 0o600)

    with pytest.raises(BlobStagingError, match="exceeds its bound"):
        with staging.stage():
            raise AssertionError("an excessive stale-file set must not be scanned")


def test_staging_cleanup_preserves_later_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "staging"
    staging = BlobStagingArea(directory)
    original_close = StagedBlobWriter.close
    original_unlink = os.unlink

    def close_then_fail(writer: StagedBlobWriter) -> None:
        original_close(writer)
        raise OSError("injected close failure")

    def interrupt_unlink(path: str, *, dir_fd: int | None = None) -> None:
        if path.startswith(".minos-blob-"):
            raise KeyboardInterrupt
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(StagedBlobWriter, "close", close_then_fail)
    monkeypatch.setattr(os, "unlink", interrupt_unlink)
    with pytest.raises(KeyboardInterrupt):
        with staging.stage():
            pass

    monkeypatch.undo()
    for entry in directory.iterdir():
        entry.unlink()
