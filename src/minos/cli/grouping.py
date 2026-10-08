"""Stdin-only `minos group` command over the shared grouping service."""

import json
import sys
from pathlib import Path

from minos.cli.grouping_output import SCHEMA, grouping_answer, human_output
from minos.domain.encounter import EncounterInputError
from minos.domain.jgp_ruleset import NoRulesetForDate
from minos.domain.reference import QueryLimitError, QueryTimeoutError, ReferenceError
from minos.grouper.pipeline import group_request
from minos.ingest.grouping_request import (
    MAX_GROUPING_REQUEST_BYTES,
    parse_grouping_request,
)
from minos.store.reference import open_reference_bundle


def execute_grouping(data: Path, *, output_json: bool) -> int:
    try:
        request = parse_grouping_request(
            sys.stdin.buffer.read(MAX_GROUPING_REQUEST_BYTES + 1)
        )
        with open_reference_bundle(data) as (connection, manifest):
            answer = grouping_answer(
                manifest, request, group_request(connection, manifest.dataset, request)
            )
        print(
            json.dumps(answer, ensure_ascii=False, sort_keys=True)
            if output_json
            else human_output(answer)
        )
        return 0
    except EncounterInputError:
        code, status = "invalid_request", 2
    except NoRulesetForDate:
        code, status = "no_ruleset_for_date", 3
    except QueryTimeoutError, QueryLimitError:
        code, status = "evaluation_limit", 4
    except ReferenceError, OSError, ValueError:
        code, status = "unavailable", 3
    print(
        json.dumps({"schema": SCHEMA, "status": "error", "error": {"code": code}})
        if output_json
        else f"minos: {code}",
        file=sys.stderr,
    )
    return status
