-- Keep walk outermost so each recursive step probes the parent index rather
-- than scanning the entire release's edges. Verified on the full archive.
WITH RECURSIVE walk(ordinal, depth) AS (
    SELECT child_ordinal, 1 FROM reference_relation
    WHERE source_release_id = :release_id AND parent_ordinal = :ordinal
      AND relation_type = :relation_type
    UNION
    SELECT relation.child_ordinal, walk.depth + 1
    FROM walk CROSS JOIN reference_relation AS relation
      ON relation.parent_ordinal = walk.ordinal
     AND relation.source_release_id = :release_id
     AND relation.relation_type = :relation_type
    WHERE walk.depth <= :max_depth
)
SELECT node.ordinal, min(walk.depth) AS depth,
       node.source_code, node.source_text, node.kind
FROM walk CROSS JOIN reference_node AS node
  ON node.source_release_id = :release_id AND node.ordinal = walk.ordinal
GROUP BY node.ordinal
ORDER BY node.ordinal
LIMIT :row_limit;
