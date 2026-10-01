SELECT node.ordinal, concept.lookup_code, node.source_code, node.source_text, node.kind
FROM json_each(:ordinals) AS requested CROSS JOIN reference_node AS node
  ON node.source_release_id = :release_id AND node.ordinal = requested.value
LEFT JOIN concept ON concept.source_release_id = node.source_release_id AND concept.node_ordinal = node.ordinal
ORDER BY node.ordinal;
