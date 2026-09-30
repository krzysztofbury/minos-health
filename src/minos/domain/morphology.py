"""Pinned offline linguistic analysis, independent of classification vocabulary."""

from importlib import import_module
from typing import Protocol, cast, final

from minos.domain.normalization import normalize_description
from minos.domain.reference import ArtifactError, QueryLimitError
from minos.domain.search import text_tokens

MORPHOLOGY_VERSION = "morfeusz2-1.99.15/sgjp-2026.06.01"
DICTIONARY_ID = "pl.sgjp.sgjp-2026.06.01"
type Analysis = tuple[int, int, tuple[str, str, str, list[str], list[str]]]


class _Analyzer(Protocol):
    def analyse(self, text: str) -> list[Analysis]: ...
    def dict_id(self) -> str: ...


class _MorfeuszModule(Protocol):
    __version__: str
    CONDITIONALLY_CASE_SENSITIVE: int
    SKIP_WHITESPACES: int

    def Morfeusz(
        self,
        *,
        dict_name: str,
        generate: bool,
        expand_dag: bool,
        expand_tags: bool,
        case_handling: int,
        whitespace: int,
    ) -> _Analyzer: ...


@final
class PolishMorphology:
    def __init__(self) -> None:
        try:
            module = cast(_MorfeuszModule, cast(object, import_module("morfeusz2")))
            if module.__version__ != "1.99.15":
                raise ArtifactError("incompatible Polish analyzer version")
            self._analyzer = module.Morfeusz(
                dict_name="sgjp",
                generate=False,
                expand_dag=False,
                expand_tags=False,
                case_handling=module.CONDITIONALLY_CASE_SENSITIVE,
                whitespace=module.SKIP_WHITESPACES,
            )
            if self._analyzer.dict_id() != DICTIONARY_ID:
                raise ArtifactError("incompatible Polish dictionary identity")
        except (ImportError, RuntimeError, OSError, AttributeError) as error:
            raise ArtifactError(
                "Polish reference search requires the pinned reference extra"
            ) from error

    def variants(self, word: str) -> tuple[str, ...]:
        if not 1 <= len(word) <= 64:
            raise QueryLimitError("morphology word exceeds length limit")
        # Short grammatical operators and every numeric/code token remain literal.
        # This includes negation; no classification-specific forms are maintained.
        if len(word) < 4 or any(character.isdecimal() for character in word):
            return (word,)
        try:
            analyses = self._analyzer.analyse(word)
        except RuntimeError as error:
            raise ArtifactError("Polish analysis failed") from error
        if len(analyses) > 256:
            raise QueryLimitError("morphology exceeds analysis limit")
        variants = {word}
        for start, _end, (surface, lemma, tag, _names, _labels) in analyses:
            if start != 0 or surface != word or tag == "ign":
                continue
            # SGJP suffixes identify homonyms, not extra words or search punctuation.
            normalized = normalize_description(lemma.split(":", 1)[0])
            if (
                4 <= len(normalized) <= 64
                and not any(character.isdecimal() for character in normalized)
                and text_tokens(normalized) == (normalized,)
            ):
                variants.add(normalized)
        if len(variants) > 16:
            raise QueryLimitError("morphology exceeds variant limit")
        return tuple(sorted(variants))
