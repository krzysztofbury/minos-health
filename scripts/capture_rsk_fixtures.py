import argparse
import json
import sys

from minos.ingest.watchers.rsk.capture import capture_private_rsk


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture bounded private RSK compatibility evidence"
    )
    _ = parser.parse_args()
    try:
        result = capture_private_rsk()
    except Exception as error:
        print(
            json.dumps(
                {
                    "event": "rsk_private_capture_failed",
                    "error_category": type(error).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "capture_id": result.capture_id,
                "resource_count": result.resource_count,
                "file_metadata_count": result.file_metadata_count,
                "captured_bytes": result.captured_bytes,
                "xml_sha256": result.xml_sha256,
                "xml_size_bytes": result.xml_size_bytes,
                "public_index_sha256": result.public_index_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
