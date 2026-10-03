from dataclasses import replace
from datetime import date
from io import BytesIO

import pytest

from minos.domain.jgp_modifiers import (
    LegalMapping,
    LegalOverlay,
    mappings,
    reconcile_legal_overlay,
    select_reviewed_mappings,
)
from minos.domain.reference import SourceError
from minos.ingest.jgp_modifiers import parse_modifiers

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<komunikat typ="CZS-PUB" wersja="1.1" kod_slow="MODTAR" rok="2026" nr-gen="100"
 data_pub="2026-08-20T08:00:30" data_gen="2026-08-20T08:37:01">
 <gruper typ_grupera="SZP">
  <modyfikatory symbol="M1" opis="Synthetic quality transition section 17(3)" status="A">
   <okres mies_od="1" mies_do="6" wart_wsp="1.025"><grupy_jgp kod_jgp="A01"/><grupy_jgp kod_jgp="A02"/></okres>
   <okres mies_od="7" mies_do="12" wart_wsp="1.20"><grupy_jgp kod_jgp="A01"/></okres>
  </modyfikatory>
  <modyfikatory symbol="M1" opis="Synthetic quality transition section 17(3)" status="A">
   <okres mies_od="7" mies_do="12" wart_wsp="1.20"><grupy_jgp kod_jgp="A02"/></okres>
  </modyfikatory>
  <modyfikatory symbol="M6a" opis="Synthetic section 17(6a) from October" status="A">
   <okres mies_od="7" mies_do="12" wart_wsp="1.1"><grupy_jgp kod_jgp="A01"/></okres>
  </modyfikatory>
  <modyfikatory symbol="INACTIVE" opis="Synthetic inactive source record" status="N"/>
 </gruper>
</komunikat>"""


def test_repeated_symbols_periods_groups_and_inactive_empty_record_keep_exact_source_order() -> (
    None
):
    release = parse_modifiers(BytesIO(XML), year=2026, generation=100)
    assert [m.symbol for m in release.modifiers] == ["M1", "M1", "M6a", "INACTIVE"]
    assert release.modifiers[0].periods[0].groups == ("A01", "A02")
    assert release.modifiers[0].periods[0].coefficient_millionths == 1025000
    assert release.modifiers[0].periods[1].coefficient_text == "1.20"
    assert release.modifiers[-1].status == "N" and release.modifiers[-1].periods == ()
    assert len(mappings(release)) == 6


@pytest.mark.parametrize(
    "before,after",
    [
        (b'typ_grupera="SZP"', b'typ_grupera="AOS"'),
        (b'rok="2026"', b'rok="2025"'),
        (b'nr-gen="100"', b'nr-gen="102"'),
        (b'status="A"', b'status="X"'),
        (b'mies_od="1"', b'mies_od="0"'),
        (b'mies_do="6"', b'mies_do="13"'),
        (b'mies_od="7"', b'mies_od="6"'),
        (b'wart_wsp="1.025"', b'wart_wsp="1e0"'),
        (b'wart_wsp="1.025"', b'wart_wsp="1.0000001"'),
        (
            b'<grupy_jgp kod_jgp="A01"/>',
            b'<grupy_jgp kod_jgp="A01"/><grupy_jgp kod_jgp="A01"/>',
        ),
        (b'<gruper typ_grupera="SZP">', b'<gruper typ_grupera="SZP" extra="meaning">'),
        (b"</gruper>", b'<unknown legal="condition"/></gruper>'),
        (b"</okres>", b"Unconsumed eligibility text</okres>"),
    ],
)
def test_malformed_or_unconsumed_modifier_source_is_not_publishable(
    before: bytes, after: bytes
) -> None:
    with pytest.raises(SourceError):
        _ = parse_modifiers(
            BytesIO(XML.replace(before, after, 1)), year=2026, generation=100
        )


def test_modifiers_refuse_dtd_entities_and_excess_bytes() -> None:
    source = XML.replace(
        b"<komunikat ", b'<!DOCTYPE komunikat [<!ENTITY x "unknown">]><komunikat ', 1
    )
    with pytest.raises(SourceError):
        _ = parse_modifiers(BytesIO(source), year=2026, generation=100)
    with pytest.raises(SourceError):
        _ = parse_modifiers(
            BytesIO(XML + b" " * (4 * 1024 * 1024)), year=2026, generation=100
        )


@pytest.mark.parametrize("code", [b"C14.", b"P42.", b"P52_", b"Z01_S"])
def test_non_catalog_source_references_survive_without_substitution(
    code: bytes,
) -> None:
    release = parse_modifiers(
        BytesIO(XML.replace(b'kod_jgp="A02"', b'kod_jgp="' + code + b'"')),
        year=2026,
        generation=100,
    )
    assert release.modifiers[0].periods[0].groups == ("A01", code.decode())


def overlay_for_source() -> LegalOverlay:
    release = parse_modifiers(BytesIO(XML), year=2026, generation=100)
    return LegalOverlay(
        2026,
        100,
        "SZP",
        "catalog-synthetic",
        date(2026, 10, 3),
        ("Synthetic owner-reviewed legal clauses, not real NFZ acceptance",),
        tuple(
            LegalMapping(
                row,
                date(2026, 10, 1) if row[0] == "M6a" else date(2026, 1, 1),
                date(2026, 12, 31),
                "Synthetic section 4(4) October start"
                if row[0] == "M6a"
                else "Synthetic section 4(3) retained quality coefficient",
            )
            for row in mappings(release)
        ),
    )


def test_explicit_legal_windows_override_publication_and_the_july_month_field() -> None:
    release = parse_modifiers(BytesIO(XML), year=2026, generation=100)
    overlay = overlay_for_source()
    selected = {
        day: select_reviewed_mappings(
            release,
            overlay,
            date.fromisoformat(day),
            catalog_release_id="catalog-synthetic",
            catalog_groups=frozenset({"A01", "A02"}),
        )
        for day in ("2026-06-30", "2026-07-01", "2026-09-30", "2026-10-01")
    }
    assert {row[5] for row in selected["2026-06-30"]} == {"1.025"}
    assert {row[5] for row in selected["2026-07-01"]} == {"1.20"}
    assert {row[0] for row in selected["2026-09-30"]} == {"M1"}
    assert {row[0] for row in selected["2026-10-01"]} == {"M1", "M6a"}
    assert all(row[2] == "A" for rows in selected.values() for row in rows)


@pytest.mark.parametrize(
    "failure", ["unbound", "year", "generation", "missing", "conflict", "group"]
)
def test_incompatible_or_unreviewed_binding_cannot_qualify_as_complete(
    failure: str,
) -> None:
    release = parse_modifiers(BytesIO(XML), year=2026, generation=100)
    overlay = overlay_for_source()
    groups = frozenset({"A01", "A02"})
    if failure == "unbound":
        with pytest.raises(SourceError, match="unbound"):
            _ = reconcile_legal_overlay(
                release,
                None,
                catalog_release_id="catalog-synthetic",
                catalog_groups=groups,
            )
        return
    elif failure == "year":
        overlay = replace(overlay, year=2025)
    elif failure == "generation":
        overlay = replace(overlay, generation=102)
    elif failure == "missing":
        overlay = replace(overlay, rows=overlay.rows[:-1])
    elif failure == "conflict":
        changed = (*overlay.rows[0].mapping[:5], "9.9", overlay.rows[0].mapping[6])
        overlay = replace(
            overlay, rows=(replace(overlay.rows[0], mapping=changed), *overlay.rows[1:])
        )
    else:
        groups = frozenset({"A01"})
    with pytest.raises(SourceError):
        _ = reconcile_legal_overlay(
            release,
            overlay,
            catalog_release_id="catalog-synthetic",
            catalog_groups=groups,
        )
