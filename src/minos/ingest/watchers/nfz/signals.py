"""Official NFZ publication cards for news, JGP notices and draft projects."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx

from minos.ingest.watchers.nfz.discovery import (
    NfzHtmlResponse,
    NfzListingError,
    fetch_nfz_html,
    parse_listing_cards,
)

type SignalChannel = Literal["news", "jgp", "project"]

_BASE = "https://www.nfz.gov.pl"
_PATHS: dict[SignalChannel, str] = {
    "news": "/aktualnosci/aktualnosci-centrali/",
    "jgp": "/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/",
    "project": "/zarzadzenia-prezesa/projekty-zarzadzen/",
}
_RELEVANCE = re.compile(r"jgp|ds[oó]z|icd[- ]?9|\b1ae?\b|szpital|rozliczani", re.I)
_MAX_SIGNALS = 64


@dataclass(frozen=True, slots=True)
class NfzSignal:
    channel: SignalChannel
    item_id: int
    title: str
    summary: str
    published_date: str
    url: str


def fetch_signal_listing(
    client: httpx.Client, channel: SignalChannel, *, page: int = 1
) -> tuple[NfzHtmlResponse, tuple[NfzSignal, ...]]:
    if channel not in _PATHS or page not in (1, 2):
        raise NfzListingError("unsupported NFZ signal listing")
    url = _BASE + _PATHS[channel]
    if page == 2:
        url += "2,strona.html"
    response = fetch_nfz_html(client, url, 256 * 1024)
    return response, parse_signal_listing(response.body, channel)


def fetch_signal_detail(client: httpx.Client, signal: NfzSignal) -> NfzHtmlResponse:
    if signal.channel not in _PATHS or not signal.url.startswith(
        _BASE + _PATHS[signal.channel]
    ):
        raise NfzListingError("NFZ signal detail URL is outside its channel")
    suffix = signal.url.removeprefix(_BASE + _PATHS[signal.channel])
    if re.fullmatch(r"[a-z0-9_-]{3,180},[1-9][0-9]{0,9}\.html", suffix) is None:
        raise NfzListingError("NFZ signal detail path is invalid")
    if not suffix.endswith(f",{signal.item_id}.html"):
        raise NfzListingError("NFZ signal detail ID conflicts with listing")
    return fetch_nfz_html(client, signal.url, 256 * 1024)


def parse_signal_listing(body: bytes, channel: SignalChannel) -> tuple[NfzSignal, ...]:
    if channel not in _PATHS:
        raise NfzListingError("NFZ signal channel is unknown")
    cards = parse_listing_cards(body)
    if len(cards) > _MAX_SIGNALS:
        raise NfzListingError("NFZ signal listing exceeds its item bound")
    signals: list[NfzSignal] = []
    seen: dict[int, NfzSignal] = {}
    for card in cards:
        title = " ".join(card.get("title", "").split())
        summary = " ".join(card.get("subject", "").split())
        raw_date = " ".join(card.get("date", "").split())
        path = card.get("href", "")
        if not title or not summary or len(title) > 512 or len(summary) > 512:
            raise NfzListingError("NFZ signal card is incomplete")
        if re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.20[0-9]{2}", raw_date) is None:
            raise NfzListingError("NFZ signal date is invalid")
        try:
            date = datetime.strptime(raw_date, "%d.%m.%Y").date().isoformat()
        except ValueError:
            raise NfzListingError("NFZ signal calendar date is invalid") from None
        match = re.fullmatch(
            re.escape(_PATHS[channel]) + r"[a-z0-9_-]{3,180},([1-9][0-9]{0,9})\.html",
            path,
        )
        if match is None:
            raise NfzListingError("NFZ signal URL is not an official detail path")
        item = NfzSignal(channel, int(match[1]), title, summary, date, _BASE + path)
        previous = seen.get(item.item_id)
        if previous is not None:
            if previous != item:
                raise NfzListingError("NFZ signal ID has conflicting cards")
            raise NfzListingError("NFZ signal ID is duplicated")
        seen[item.item_id] = item
        if channel == "jgp" or _RELEVANCE.search(title + " " + summary):
            signals.append(item)
    if not signals and channel == "jgp":
        raise NfzListingError("NFZ JGP listing has no notices")
    return tuple(signals)
