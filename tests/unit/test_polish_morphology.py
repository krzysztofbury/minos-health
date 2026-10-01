from importlib import import_module

import pytest

from minos.domain.morphology import PolishMorphology
from minos.domain.reference import ArtifactError


@pytest.mark.parametrize(
    ("form", "lemma"),
    [
        ("cukrzycy", "cukrzyca"),
        ("niewydolności", "niewydolność"),
        ("świadczeń", "świadczenie"),
        ("załącznika", "załącznik"),
        ("zarządzeniami", "zarządzenie"),
    ],
)
def test_real_polish_dictionary_supplies_medical_and_document_inflections(
    form: str, lemma: str
) -> None:
    variants = PolishMorphology().variants(form)
    assert form in variants
    assert lemma in variants


@pytest.mark.parametrize("word", ["bez", "nie", "2", "e11", "qzxwqzxw"])
def test_negation_numeric_identifiers_and_unknown_words_stay_literal(word: str) -> None:
    assert PolishMorphology().variants(word) == (word,)


def test_ambiguous_analysis_keeps_alternatives_instead_of_picking_the_first() -> None:
    variants = PolishMorphology().variants("cukrzycy")
    assert "cukrzyca" in variants
    assert "cukrzyk" in variants


@pytest.mark.parametrize("word", ["bzami", "niego", "typami"])
def test_long_forms_cannot_introduce_short_grammatical_operators(word: str) -> None:
    assert all(
        len(alternative) >= 4 for alternative in PolishMorphology().variants(word)
    )


def test_dictionary_identity_drift_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "minos.domain.morphology.DICTIONARY_ID", "unreviewed-dictionary"
    )
    with pytest.raises(ArtifactError, match="dictionary identity"):
        _ = PolishMorphology()


def test_analyzer_version_drift_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_module("morfeusz2"), "__version__", "unreviewed-version")
    with pytest.raises(ArtifactError, match="analyzer version"):
        _ = PolishMorphology()
