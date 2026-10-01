-- Canonical codes absent from the supporting hierarchy are listed, never re-parented.
SELECT concept.node_ordinal, concept.lookup_code, node.source_text
FROM concept
CROSS JOIN reference_node AS node
  ON node.source_release_id = :release_id AND node.ordinal = concept.node_ordinal
WHERE concept.source_release_id = :release_id
  AND NOT EXISTS (
    SELECT 1 FROM concept AS context
    WHERE context.source_release_id = :hierarchy_release_id
      AND context.lookup_code = concept.lookup_code
  )
ORDER BY concept.lookup_code
LIMIT :row_limit;
