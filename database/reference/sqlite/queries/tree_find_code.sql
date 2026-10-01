SELECT concept.node_ordinal
FROM concept
CROSS JOIN reference_node AS node
  ON node.source_release_id = concept.source_release_id AND node.ordinal = concept.node_ordinal
WHERE concept.source_release_id = :release_id AND concept.lookup_code = :code
  AND node.kind IN ('range', 'classification');
