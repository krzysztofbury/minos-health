"""Explicit versioned output adapters; domain values have no JSON policy."""

from minos.domain.morphology import MORPHOLOGY_VERSION
from minos.domain.reference import ArtifactError
from minos.domain.search import ContextNode, SearchHit, SearchResult, SupportingContext
from minos.store.artifact import ArtifactManifest, dataset_identity


def search_envelope(
    result: SearchResult,
    manifest: ArtifactManifest,
    *,
    disclaimer: str,
) -> dict[str, object]:
    source = next(
        (
            source
            for source in manifest.dataset.sources
            if source.release_id == result.release_id
        ),
        None,
    )
    if source is None:
        raise ArtifactError("search release is not in the verified manifest")
    return {
        "version": 1,
        "status": "ok" if result.hits else "no_results",
        "system": result.system,
        "release_id": result.release_id,
        "language": result.language,
        "dataset_id": dataset_identity(manifest.dataset),
        "database_sha256": manifest.database_sha256,
        "search_version": result.search_version,
        "morphology_version": MORPHOLOGY_VERSION,
        "index_used": result.index_used,
        "effective_query": result.query.effective_text,
        "literal_query": result.query.literal_text,
        "interpretation": list(result.query.interpretation),
        "morphology": [
            {"token": token, "alternatives": list(alternatives)}
            for token, alternatives in result.morphology
        ],
        "truncated": result.truncated,
        "results": [
            _hit_envelope(hit, manifest, result.release_id) for hit in result.hits
        ],
        "suggestions": [
            {
                "query": item.query,
                "replacement": item.replacement,
                "distance": item.distance,
            }
            for item in result.suggestions
        ],
        "provenance": {
            "publisher": source.publisher,
            "source_version": source.source_version,
            "source_sha256": source.sha256,
            "license": source.license,
            "notices": list(source.notices),
        },
        "disclaimer": disclaimer,
    }


def _hit_envelope(
    hit: SearchHit, manifest: ArtifactManifest, release_id: str
) -> dict[str, object]:
    return {
        "node_ordinal": hit.node_ordinal,
        "code": hit.code,
        "source_code": hit.source_code,
        "label": hit.label,
        "kind": hit.kind,
        "source_attributes": dict(hit.source_attributes),
        "match_type": hit.match_type,
        "synonym_source_wording": hit.synonym_source_wording,
        "jgp_membership": {
            "group_count": hit.jgp_group_count,
            "service_date": hit.jgp_service_date.isoformat()
            if hit.jgp_service_date
            else None,
            "meaning": "Explicit attachment 9 membership only; not typical treatment or encounter eligibility.",
        },
        "match": {
            "node_ordinal": hit.matched_node_ordinal,
            "text": hit.matched_text,
            "description_type": hit.description_type,
            "language": hit.language,
            "source_attributes": dict(hit.matched_source_attributes),
        },
        "context": _context_envelope(hit.context_nodes, hit.context_edges),
        "supporting_contexts": [
            _supporting_envelope(context, manifest, release_id)
            for context in hit.supporting_contexts
        ],
    }


def _supporting_envelope(
    context: SupportingContext, manifest: ArtifactManifest, release_id: str
) -> dict[str, object]:
    source = next(
        (
            source
            for source in manifest.dataset.sources
            if source.release_id == context.release_id
        ),
        None,
    )
    if source is None or source.context_for != release_id:
        raise ArtifactError("supporting context is not bound to the canonical release")
    return {
        "release_id": source.release_id,
        "node_ordinal": context.node_ordinal,
        "publisher": source.publisher,
        "source_version": source.source_version,
        "source_sha256": source.sha256,
        "source_url": source.source_url,
        "license": source.license,
        "notices": list(source.notices),
        "context": _context_envelope(context.nodes, context.edges),
    }


def _context_envelope(
    nodes: tuple[ContextNode, ...], edges: tuple[tuple[int, int], ...]
) -> dict[str, object]:
    return {
        "nodes": [
            {
                "ordinal": node.ordinal,
                "code": node.code,
                "source_code": node.source_code,
                "label": node.label,
                "kind": node.kind,
            }
            for node in nodes
        ],
        "edges": [
            {"parent": parent, "child": child, "type": "contains"}
            for parent, child in edges
        ],
    }


def search_table(
    result: SearchResult, manifest: ArtifactManifest, *, disclaimer: str
) -> str:
    lines = [
        f"Dataset: {dataset_identity(manifest.dataset)} | {result.system} | {result.release_id}",
        "CODE       KIND             SOURCE LABEL",
    ]
    if result.query.interpretation:
        lines.append("Interpreted query: " + result.query.effective_text)
    if result.morphology:
        lines.append(
            "Polish analysis ["
            + MORPHOLOGY_VERSION
            + "]: "
            + "; ".join(
                token + " -> " + ", ".join(alternatives)
                for token, alternatives in result.morphology
            )
        )
    for hit in result.hits:
        lines.append(
            f"{hit.code or '(uncoded)':<10} {hit.kind:<16} {_display(hit.label)}"
        )
        lines.append(
            f"  Match [{hit.match_type}/{hit.description_type}]: {_display(hit.matched_text)}"
        )
        if hit.source_attributes:
            lines.append(
                "  Source attributes: "
                + "; ".join(
                    name + "=" + _display(text) for name, text in hit.source_attributes
                )
            )
        if (
            hit.matched_source_attributes
            and hit.matched_node_ordinal != hit.node_ordinal
        ):
            lines.append(
                "  Match source attributes: "
                + "; ".join(
                    name + "=" + _display(text)
                    for name, text in hit.matched_source_attributes
                )
            )
        lines.append(
            "  Context nodes: "
            + "; ".join(
                f"{node.ordinal}: {node.code or '(uncoded)'} {_display(node.label)}"
                for node in hit.context_nodes
            )
        )
        lines.append(
            "  Contains edges: "
            + ", ".join(f"{parent}->{child}" for parent, child in hit.context_edges)
        )
        for context in hit.supporting_contexts:
            lines.append(
                "  Supporting hierarchy (not canonical): " + context.release_id
            )
            lines.extend(
                f"    {node.ordinal}: {node.code or '(uncoded)'} {_display(node.label)}"
                for node in context.nodes
            )
            lines.append(
                "    Contains edges: "
                + ", ".join(f"{parent}->{child}" for parent, child in context.edges)
            )
    if not result.hits:
        lines.append("No matching source entries.")
    if result.truncated:
        lines.append("More matches exist; narrow the query or increase --limit.")
    for suggestion in result.suggestions:
        lines.append("Did you mean: " + suggestion.query + "?")
    lines.append(disclaimer)
    return "\n".join(lines)


def _display(text: str) -> str:
    return " ".join(text.split())
