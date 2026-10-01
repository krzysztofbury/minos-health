WITH RECURSIVE attribution(document_id, source_release_id, ordinal, depth) AS (
    SELECT id, source_release_id, node_ordinal, 0 FROM search_document
    UNION
    SELECT walk.document_id, walk.source_release_id, relation.parent_ordinal, walk.depth + 1
    FROM attribution AS walk CROSS JOIN reference_relation AS relation
    ON relation.source_release_id = walk.source_release_id AND relation.child_ordinal = walk.ordinal
    WHERE relation.relation_type = 'contains' AND walk.depth < 65
      AND NOT EXISTS (SELECT 1 FROM concept WHERE source_release_id = walk.source_release_id AND node_ordinal = walk.ordinal)
)
SELECT document_id, source_release_id, ordinal, min(depth)
FROM attribution GROUP BY document_id, source_release_id, ordinal;
