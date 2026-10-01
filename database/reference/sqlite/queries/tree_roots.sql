-- Parentless nodes scan the release once; callers cache this immutable answer.
SELECT 0, node.ordinal, node.kind, node.source_code, node.source_text,
       concept.lookup_code
FROM reference_node AS node
LEFT JOIN concept
  ON concept.source_release_id = :release_id AND concept.node_ordinal = node.ordinal
WHERE node.source_release_id = :release_id
  AND node.kind <> 'text'
  AND NOT EXISTS (
    SELECT 1 FROM reference_relation AS relation INDEXED BY reference_relation_child
    WHERE relation.source_release_id = :release_id
      AND relation.child_ordinal = node.ordinal
      AND relation.relation_type = 'contains'
  )
ORDER BY node.ordinal
LIMIT :row_limit;
