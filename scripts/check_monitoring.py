"""Read production health from public endpoints only, with no credentials (#279).

Uptime Kuma watches the public edge, every watcher and the data update and
activation runs, and sends each down state to the operator's Telegram alert.
This reads the same monitors from the public status page, plus `/readyz`
directly, so an agent can answer "is production healthy?" without SSH. It also
catches the one failure Kuma cannot report about itself: a Kuma that stopped
checking, seen as a stale edge heartbeat, means no alert reaches Telegram.
"""

import argparse
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from http.client import HTTPResponse
from typing import ClassVar, cast

from pydantic import BaseModel, ConfigDict, Field

STATUS = "https://status.minoshealth.pl/api/status-page/minos-health"
HEARTBEATS = "https://status.minoshealth.pl/api/status-page/heartbeat/minos-health"
READYZ = "https://minoshealth.pl/readyz"
TIMEOUT_S = 10
MAX_BYTES = 1024 * 1024
# The edge monitor beats every 60 seconds; this many seconds without one means
# Kuma itself stopped, and with it every Telegram alert.
STALE_EDGE_S = 600
MAX_MESSAGE = 120

type Fetch = Callable[[str], object]


class Monitor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    id: int
    name: str = Field(max_length=150)
    type: str = Field(max_length=32)


class Group(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    monitorList: list[Monitor]  # noqa: N815 - Kuma's field name


class Page(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    publicGroupList: list[Group]  # noqa: N815


class Beat(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    status: int
    time: str
    msg: str | None = None


class Beats(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")
    heartbeatList: dict[str, list[Beat]]  # noqa: N815
    uptimeList: dict[str, float]  # noqa: N815


def fetch(url: str) -> object:
    # Cloudflare refuses Python's default User-Agent with 403.
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "minos-monitoring-check"},
    )
    with cast(HTTPResponse, urllib.request.urlopen(request, timeout=TIMEOUT_S)) as r:
        content = r.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError(f"{url} answer exceeds {MAX_BYTES} bytes")
    return cast(object, json.loads(content))


def age_s(beat: Beat, now: datetime) -> int:
    # Kuma 2 reports heartbeat times in UTC without a zone.
    moment = datetime.fromisoformat(beat.time).replace(tzinfo=UTC)
    return round((now - moment).total_seconds())


def check(now: datetime, get: Fetch = fetch) -> dict[str, object]:
    page = Page.model_validate(get(STATUS))
    beats = Beats.model_validate(get(HEARTBEATS))
    monitors: list[dict[str, object]] = []
    problems: list[str] = []
    for monitor in (m for group in page.publicGroupList for m in group.monitorList):
        history = beats.heartbeatList.get(str(monitor.id), [])
        last = history[-1] if history else None
        row: dict[str, object] = {
            "name": monitor.name,
            "type": monitor.type,
            "up": last is not None and last.status == 1,
            "last_beat_age_s": None if last is None else age_s(last, now),
            "uptime_24h": beats.uptimeList.get(f"{monitor.id}_24"),
        }
        if last is None or last.status != 1:
            problems.append(f"{monitor.name} is not up")
            # Watcher pushes carry only a bounded failure category.
            row["message"] = (last.msg or "")[:MAX_MESSAGE] if last else None
        monitors.append(row)
    edges = [row for row in monitors if row["type"] == "http"]
    stale = not edges or any(
        row["last_beat_age_s"] is None
        or cast(int, row["last_beat_age_s"]) > STALE_EDGE_S
        for row in edges
    )
    if stale:
        problems.append("Kuma has stopped checking: Telegram alerts are not sent")
    try:
        ready = cast(dict[str, object], get(READYZ)).get("status") == "ready"
    except OSError, ValueError:
        ready = False
    if not ready:
        problems.append("public /readyz is not ready")
    return {
        "event": "monitoring_checked",
        "healthy": not problems,
        "problems": problems,
        "kuma_checking": not stale,
        "readyz_ready": ready,
        "monitors": monitors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.parse_args()
    try:
        result = check(datetime.now(UTC))
    except (OSError, ValueError) as error:
        # The status page itself is unreachable: Kuma, the tunnel or the host
        # is down, and Kuma cannot alert about any of them.
        status = error.code if isinstance(error, urllib.error.HTTPError) else None
        print(json.dumps({"event": "monitoring_unreachable", "healthy": False,
                          "error": type(error).__name__, "status": status}))  # fmt: skip
        raise SystemExit(2) from None
    print(json.dumps(result))
    raise SystemExit(0 if result["healthy"] else 1)


if __name__ == "__main__":
    main()
