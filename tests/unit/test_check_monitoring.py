from datetime import UTC, datetime
from typing import cast

import pytest

from scripts import check_monitoring

NOW = datetime(2026, 10, 28, 10, 0, 0, tzinfo=UTC)
PAGE = {
    "config": {"slug": "minos-health"},
    "publicGroupList": [
        {
            "name": "Services",
            "monitorList": [
                {"id": 1, "name": "Minos public edge", "type": "http"},
                {"id": 2, "name": "RSK watcher", "type": "push"},
            ],
        }
    ],
}


def beats(edge: dict[str, object], watcher: list[dict[str, object]]) -> object:
    return {
        "heartbeatList": {"1": [edge], "2": watcher},
        "uptimeList": {"1_24": 0.999, "2_24": 1},
    }


UP_EDGE: dict[str, object] = {
    "status": 1,
    "time": "2026-10-28 09:59:30.000",
    "msg": "200 - OK",
}
UP_WATCHER: list[dict[str, object]] = [
    {"status": 1, "time": "2026-10-28 03:00:11.440", "msg": "OK"}
]


def fetcher(answers: dict[str, object]) -> check_monitoring.Fetch:
    def get(url: str) -> object:
        answer = answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return get


def answers(**changes: object) -> dict[str, object]:
    return {
        check_monitoring.STATUS: PAGE,
        check_monitoring.HEARTBEATS: changes.get(
            "heartbeats", beats(UP_EDGE, UP_WATCHER)
        ),
        check_monitoring.READYZ: changes.get("readyz", {"status": "ready"}),
    }


def test_all_monitors_up_and_fresh_is_healthy() -> None:
    result = check_monitoring.check(NOW, fetcher(answers()))
    assert result["healthy"] is True and result["problems"] == []
    assert result["monitors"] == [
        {"name": "Minos public edge", "type": "http", "up": True,
         "last_beat_age_s": 30, "uptime_24h": 0.999},
        {"name": "RSK watcher", "type": "push", "up": True,
         "last_beat_age_s": 25189, "uptime_24h": 1},
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        # A watcher that reported failure, or stopped pushing (Kuma marks it down).
        (
            {
                "heartbeats": beats(
                    UP_EDGE,
                    [
                        {
                            "status": 0,
                            "time": "2026-10-28 09:00:00",
                            "msg": "failed: download" + "x" * 300,
                        }
                    ],
                )
            },
            "RSK watcher is not up",
        ),  # fmt: skip
        ({"heartbeats": beats(UP_EDGE, [])}, "RSK watcher is not up"),
        # Kuma stopped checking: every monitor still looks up, but no alert is sent.
        (
            {
                "heartbeats": beats(
                    {**UP_EDGE, "time": "2026-10-28 09:40:00"}, UP_WATCHER
                )
            },
            "Kuma has stopped checking: Telegram alerts are not sent",
        ),
        ({"readyz": OSError("refused")}, "public /readyz is not ready"),
        ({"readyz": {"status": "starting"}}, "public /readyz is not ready"),
    ],
)
def test_each_failure_is_named(change: dict[str, object], problem: str) -> None:
    result = check_monitoring.check(NOW, fetcher(answers(**change)))
    assert result["healthy"] is False
    assert result["problems"] == [problem]
    for row in cast(list[dict[str, object]], result["monitors"]):
        assert len(str(row.get("message") or "")) <= check_monitoring.MAX_MESSAGE
