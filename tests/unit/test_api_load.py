import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast, override

import pytest

from scripts import measure_api_load
from scripts.measure_api_load import Step, mcp_steps, phase, workload

RELEASES: dict[str, object] = {
    "dataset_id": "d",
    "releases": [
        {"system": "icd10-pl", "release_id": "icd10-r"},
        {"system": "icd9-pl", "release_id": "icd9-r"},
    ],
}


class Handler(BaseHTTPRequestHandler):
    # Concept lookups fail with a server error, tree lookups with the closed
    # deadline answer, Gruper refuses as busy, MCP answers a tool error;
    # everything else succeeds.
    def _answer(self, status: int, body: object) -> None:
        content = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        _ = self.wfile.write(content)

    def do_GET(self) -> None:
        if self.path.startswith("/api/v1/concepts/"):
            self._answer(503, {"api": 1, "error": {"code": "unavailable"}})
        elif self.path.startswith("/api/v1/tree/"):
            self._answer(504, {"api": 1, "error": {"code": "query_timeout"}})
        else:
            self._answer(200, {"api": 1})

    def do_POST(self) -> None:
        _ = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/api/v1/group":
            self._answer(429, {"api": 1, "error": {"code": "busy"}})
        elif self.path == "/mcp":
            self._answer(
                200,
                {
                    "result": {
                        "isError": True,
                        "structuredContent": {"error": {"code": "BUSY"}},
                    }
                },
            )
        else:
            self._answer(200, {"api": 1, "results": []})

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def base() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def kinds(result: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], result["kinds"])


def test_reports_closed_codes_and_no_request_content(base: str) -> None:
    steps = workload(RELEASES) + mcp_steps()

    result = phase(
        base, steps, 2, len(steps) * 2, stop_on_error=False, distinct_clients=True
    )

    measured = kinds(result)
    assert measured["group"]["errors"] == {"busy": 2}
    assert measured["mcp_group_case"]["errors"] == {"BUSY": 2}
    assert measured["concept"]["statuses"] == {"503": 2}
    assert measured["search"]["errors"] == {}
    # Failed requests do not count towards latency percentiles.
    assert measured["group"]["p95_ms"] is None
    report = json.dumps(result)
    for secret in ("zapalenie", "J18.9", "K40.9", "icd10-r", "198.51.100"):
        assert secret not in report


def test_a_public_phase_stops_at_the_first_server_error(base: str) -> None:
    concept = Step("concept", "/api/v1/concepts/icd10-pl/r/J18.9")
    search = Step("search", "/api/v1/search", {"query": "x"})

    result = phase(
        base, [search, concept], 1, 40, stop_on_error=True, distinct_clients=False
    )

    assert result["stopped_early"] is True
    assert cast(int, result["requests"]) < 40


def test_a_closed_deadline_answer_does_not_stop_a_public_phase(base: str) -> None:
    tree = Step("tree", "/api/v1/tree/icd10-r/A00")
    search = Step("search", "/api/v1/search", {"query": "x"})

    result = phase(
        base, [tree, search], 1, 10, stop_on_error=True, distinct_clients=False
    )

    assert result["stopped_early"] is False
    assert kinds(result)["tree"]["errors"] == {"query_timeout": 5}


def test_public_runs_require_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        _ = measure_api_load.public("http://minoshealth.pl")
