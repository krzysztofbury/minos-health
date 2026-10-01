import pytest

from minos.domain.search import (
    SearchInputError,
    SearchRequest,
    prepare_query,
)


def test_polish_lookup_wrapper_preserves_literal_forms_and_qualifiers() -> None:
    query = prepare_query(" Pokaż mi możliwe kody dla cukrzycy typu 2 bez powikłań ")
    assert query.effective_text == "cukrzycy typu 2 bez powikłań"
    assert query.tokens == ("cukrzycy", "typu", "2", "bez", "powikłań")
    assert query.interpretation == ("polish_lookup_wrapper",)
    assert not query.code_like


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        "***",
        "a" * 257,
        "x\x1b[31m",
        "one two three four five six seven eight nine",
    ],
)
def test_bad_queries_do_not_become_unbounded_or_executable_searches(text: str) -> None:
    with pytest.raises(SearchInputError):
        _ = prepare_query(text)


def test_literal_operator_words_and_polish_letters_are_retained() -> None:
    query = prepare_query('ŻÓŁĆ OR "Łódź"')
    assert query.tokens == ("żółć", "or", "łódź")
    assert prepare_query("e11.0").code_key == "E11.0"
    assert prepare_query("e11.0").code_like
    assert prepare_query("e110").code_key == "E110"


def test_result_count_and_language_are_bounded_before_storage() -> None:
    with pytest.raises(SearchInputError):
        _ = SearchRequest("icd10-pl", prepare_query("E11"), limit=101)
    with pytest.raises(SearchInputError):
        _ = SearchRequest("icd10-pl", prepare_query("E11"), language="de")
