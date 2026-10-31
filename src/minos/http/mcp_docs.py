"""The MCP reference on /dla-agentow, generated from the live tool catalog (#216).

Descriptions, input tables and output fields come from `tools/list`, so the
page cannot drift from the server. Only the example arguments are written by
hand, and a test validates each against its tool's published input schema.
"""

import asyncio
import html
import json
from pathlib import Path
from typing import Final, cast

from fastapi import FastAPI
from mcp.types import Tool as MCPTool

from minos.http.mcp import MCP_PATH, SITE, create_mcp, inline_refs

type Json = dict[str, object]

EXAMPLES: Final[dict[str, Json]] = {
    "get_service_info": {},
    "search_codes": {"query": "zapalenie płuc", "systems": ["icd10-pl", "jgp"]},
    "get_code": {"system": "icd10-pl", "code": "J18.9", "service_date": "2026-10-22"},
    "browse_codes": {"system": "icd11-pl", "parent": "11"},
    "map_icd10_icd11": {"code": "I10", "direction": "to_icd11"},
    "get_jgp_group": {"code": "F72", "service_date": "2026-10-22"},
    "get_jgp_list": {"list_id": 1, "catalog": "1a", "page": 1},
    "browse_jgp_catalog": {"catalog": "1a", "section": 1},
    "compare_jgp_group": {
        "code": "A01",
        "before_date": "2026-05-15",
        "after_date": "2026-10-22",
    },
    "get_case_dictionaries": {},
    "group_case": {
        "case": {
            "admitted_on": "2026-09-28",
            "discharged_on": "2026-10-02",
            "hospitalization_type": "hospitalization",
            "age_completed_years": 51,
            "principal_diagnosis": {"code": "K40.9", "hospitalization_level": True},
            "stays": [
                {
                    "identifier": "surgery",
                    "department": "4500",
                    "admitted_on": "2026-09-28",
                    "discharged_on": "2026-10-02",
                }
            ],
            "procedures": [
                {"code": "53.03", "performed_on": "2026-10-01", "stay_id": "surgery"}
            ],
            "product_reporting": "complete",
        }
    },
    "get_source_status": {"source": "rsk"},
}
ERRORS: Final = (
    (
        "INVALID_INPUT",
        "Arguments do not match the input schema or the API refused them.",
    ),
    ("NOT_FOUND", "No such code, group, list or release."),
    (
        "NO_RELEASE_FOR_DATE",
        "No catalog or dictionary release covers the service date.",
    ),
    ("NOT_COVERED", "The sources cannot answer this date or comparison unambiguously."),
    ("BUSY", "The service is at capacity; retry after a short pause."),
    ("TIMEOUT", "The bounded query ran out of time; this is not an empty answer."),
    ("LIMIT", "The result would exceed 256 KiB; ask for a narrower answer."),
    ("UNAVAILABLE", "A data source is temporarily unreadable."),
    ("INTERNAL_ERROR", "An unexpected failure; no details are returned."),
)


def catalog() -> list[MCPTool]:
    """The tool list exactly as `tools/list` serves it."""
    server, close = create_mcp(FastAPI(), Path("/nonexistent"))

    async def listed() -> list[MCPTool]:
        try:
            return await server.list_tools()
        finally:
            await close()

    return asyncio.run(listed())


def _type(schema: Json) -> str:
    if "enum" in schema:
        return " | ".join(
            json.dumps(item, ensure_ascii=False)
            for item in cast(list[object], schema["enum"])
        )
    if "anyOf" in schema:
        return " or ".join(
            _type(cast(Json, item))
            for item in cast(list[object], schema["anyOf"])
            if isinstance(item, dict)
        )
    kind = schema.get("type")
    if kind == "array":
        items = schema.get("items")
        return (
            f"array of {_type(cast(Json, items))}"
            if isinstance(items, dict)
            else "array"
        )
    if kind == "string" and schema.get("format") == "date":
        return "date (YYYY-MM-DD)"
    return str(kind or "object")


def _constraints(schema: Json) -> str:
    parts: list[str] = []
    for key, label in (
        ("minLength", "min length"),
        ("maxLength", "max length"),
        ("minimum", "min"),
        ("maximum", "max"),
        ("minItems", "min items"),
        ("maxItems", "max items"),
        ("pattern", "pattern"),
    ):
        if key in schema:
            parts.append(f"{label} {schema[key]}")
    for item in cast(list[object], schema.get("anyOf") or []):
        if isinstance(item, dict):
            nested = _constraints(cast(Json, item))
            if nested:
                parts.append(nested)
    return ", ".join(parts)


def _inputs(tool: MCPTool) -> str:
    schema = inline_refs(cast(Json, tool.input_schema))
    properties = cast(dict[str, object], schema.get("properties") or {})
    required = set(cast(list[str], schema.get("required") or []))
    if not properties:
        return "<p>No arguments.</p>"
    rows: list[str] = []
    for name, value in properties.items():
        field = cast(Json, value)
        kind = "object (see the stay fields below)" if name == "case" else _type(field)
        default = (
            html.escape(json.dumps(field["default"], ensure_ascii=False))
            if "default" in field
            else ""
        )
        note = field.get("description")
        rows.append(
            f"<tr><td><code>{html.escape(name)}</code>"
            + (
                f'<span class="mcp-note">{html.escape(note)}</span>'
                if isinstance(note, str)
                else ""
            )
            + f"</td><td>{html.escape(kind)}</td>"
            + f"<td>{'yes' if name in required else 'no'}</td><td>{default}</td>"
            + f"<td>{html.escape(_constraints(field))}</td></tr>"
        )
    return (
        '<div class="table-scroll"><table class="mcp-table"><thead><tr><th>Argument</th>'
        + "<th>Type</th><th>Required</th><th>Default</th><th>Constraints</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _stay_fields(tool: MCPTool) -> str:
    case = cast(
        Json,
        cast(
            dict[str, object], cast(Json, tool.input_schema).get("properties") or {}
        ).get("case")
        or {},
    )
    properties = cast(dict[str, object], case.get("properties") or {})
    required = set(cast(list[str], case.get("required") or []))
    items = "".join(
        f"<li><code>{html.escape(name)}</code>{' (required)' if name in required else ''}: "
        + f"{html.escape(_type(cast(Json, value)))}</li>"
        for name, value in properties.items()
    )
    return f"<p>Stay fields (<code>case</code>):</p><ul>{items}</ul>" if items else ""


def _outputs(tool: MCPTool) -> str:
    schema = cast(Json, tool.output_schema or {})
    names = list(cast(dict[str, object], schema.get("properties") or {}))
    if not names:
        return ""
    fields = ", ".join(f"<code>{html.escape(name)}</code>" for name in names)
    return f"<p>Returns: {fields}; answers from the HTTP API may add fields.</p>"


def reference_html(tools: list[MCPTool]) -> str:
    """One section per tool plus the error codes, as page content."""
    sections: list[str] = []
    for tool in tools:
        example = EXAMPLES[tool.name]
        call = json.dumps(
            {"name": tool.name, "arguments": example}, ensure_ascii=False, indent=2
        )
        sections.append(
            f'        <details class="fold mcp-tool" id="{html.escape(tool.name)}">'
            + f'<summary><h3 class="fold-title">{html.escape(tool.title or "")} <code>{html.escape(tool.name)}</code></h3></summary>'
            + '<div class="fold-body">'
            + f"<p>{html.escape(tool.description or '')}</p>"
            + _inputs(tool)
            + (_stay_fields(tool) if tool.name == "group_case" else "")
            + _outputs(tool)
            + f"<p>Example call:</p><pre><code>{html.escape(call)}</code></pre>"
            + "</div></details>"
        )
    errors = "".join(
        f"<tr><td><code>{code}</code></td><td>{html.escape(meaning)}</td></tr>"
        for code, meaning in ERRORS
    )
    contents = "".join(
        f'<li><a href="#{html.escape(tool.name)}"><code>{html.escape(tool.name)}</code></a></li>'
        for tool in tools
    )
    return (
        f'        <nav aria-label="Narzędzia"><ul class="mcp-contents">{contents}</ul></nav>\n'
        + "\n".join(sections)
        + '\n        <details class="fold mcp-tool" id="errors"><summary><h3 class="fold-title">Error codes</h3></summary><div class="fold-body">'
        + "<p>Failures return <code>isError: true</code> and"
        + ' <code>{"error": {"code": "…"}}</code>, never input or exception text.</p>'
        + '<div class="table-scroll"><table class="mcp-table"><thead><tr><th>Code</th>'
        + f"<th>Meaning</th></tr></thead><tbody>{errors}</tbody></table></div></div></details>"
    )


ENDPOINT: Final = SITE + MCP_PATH
