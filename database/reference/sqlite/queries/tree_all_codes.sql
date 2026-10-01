-- Every structural code in source order, for the per-dictionary sitemap.
SELECT concept.lookup_code
FROM concept
CROSS JOIN reference_node AS node
  ON node.source_release_id = concept.source_release_id AND node.ordinal = concept.node_ordinal
WHERE concept.source_release_id = :release_id AND node.kind IN ('range', 'classification')
ORDER BY node.ordinal
LIMIT :row_limit;
