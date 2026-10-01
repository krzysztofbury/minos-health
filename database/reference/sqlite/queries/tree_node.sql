SELECT node.ordinal, node.kind, node.source_code, node.source_text, concept.lookup_code
FROM reference_node AS node
LEFT JOIN concept
  ON concept.source_release_id = :release_id AND concept.node_ordinal = node.ordinal
WHERE node.source_release_id = :release_id AND node.ordinal = :ordinal;
