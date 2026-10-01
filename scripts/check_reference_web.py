"""Bounded public/local deployment smoke and representative reference API load."""

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPResponse
from pathlib import Path
from typing import cast, final


def get_json(
    base: str, route: str, body: Mapping[str, object] | None = None
) -> tuple[int, dict[str, object], float]:
    request = urllib.request.Request(
        base + route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Host": "minoshealth.pl", "Content-Type": "application/json"},
    )
    began = time.perf_counter()
    try:
        with cast(HTTPResponse, urllib.request.urlopen(request, timeout=5)) as response:
            content = response.read(1_048_577)
            assert len(content) <= 1_048_576
            answer = cast(dict[str, object], json.loads(content))
            return response.status, answer, (time.perf_counter() - began) * 1000
    except urllib.error.HTTPError as error:
        _ = error.read(4096)
        error.close()
        return error.code, {}, (time.perf_counter() - began) * 1000


def check(base: str, *, expected_digest: str | None = None) -> dict[str, object]:
    status, ready, _ = get_json(base, "/readyz")
    assert status == 200
    assert ready["status"] == "ready"
    status, metadata, _ = get_json(base, "/api/v1/releases")
    assert status == 200
    sources = cast(list[dict[str, str]], metadata["releases"])
    assert len(sources) == 2
    assert {source["origin"] for source in sources} == {"czs", "nfz-workbook"}
    bodies = [
        {"system": "icd9-pl", "release_id": source["release_id"], "query": query}
        for source in sources
        for query in ("A01", "badania moczu")
    ]

    def lookup(index: int) -> tuple[int, float]:
        body = bodies[index % len(bodies)]
        status, answer, elapsed_ms = get_json(base, "/api/v1/search", body)
        if status == 200:
            assert answer["api"] == 1
            assert answer["release_id"] == body["release_id"]
            assert answer["dataset_id"] == metadata["dataset_id"]
            if expected_digest is not None:
                assert answer["database_sha256"] == expected_digest
            hits = cast(list[dict[str, object]], answer["results"])
            assert hits
            if body["query"] == "A01":
                assert hits[0]["code"] == "A01"
                assert hits[0]["match_type"] == "exact_code"
        return status, elapsed_ms

    for index in range(len(bodies)):
        status, _ = lookup(index)
        assert status == 200
    with ThreadPoolExecutor(max_workers=4) as executor:
        measured = list(executor.map(lookup, range(48)))
    assert {status for status, _ in measured} == {200}
    samples = sorted(duration for _, duration in measured)
    p95_ms = samples[math.ceil(len(samples) * 0.95) - 1]
    assert p95_ms < 500, f"validation p95 budget exceeded: {p95_ms:.1f} ms"
    with ThreadPoolExecutor(max_workers=8) as executor:
        overload = list(executor.map(lookup, range(32)))
    statuses = {
        str(status): sum(1 for code, _ in overload if code == status)
        for status, _ in overload
    }
    assert {status for status, _ in overload} <= {200, 429, 503}
    assert get_json(base, "/healthz")[0] == 200
    assert get_json(base, "/readyz")[0] == 200
    assert get_json(base, "/api/v1/search?q=PRIVATE_QUERY_SENTINEL")[0] == 404
    assert get_json(base, "/api/v1/artifacts/private")[0] == 404
    return {
        "dataset_id": metadata["dataset_id"],
        "sample_count": len(samples),
        "concurrency": 4,
        "p50_ms": round(samples[len(samples) // 2], 3),
        "p95_ms": round(p95_ms, 3),
        "overload_concurrency": 8,
        "overload_statuses": statuses,
        "readiness_after_load": "passed",
        "url_queries_and_private_archive": "rejected",
    }


@final
class Arguments(argparse.Namespace):
    base: str = "https://minoshealth.pl"
    output: Path = Path()
    expected_digest: str | None = None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    _ = parser.add_argument("--base", default="https://minoshealth.pl")
    _ = parser.add_argument("--expected-digest")
    arguments = Arguments()
    _parsed_arguments = parser.parse_args(namespace=arguments)
    report = check(arguments.base, expected_digest=arguments.expected_digest)
    with arguments.output.open("x") as stream:
        _written = stream.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
