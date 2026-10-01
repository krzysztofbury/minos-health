-- Supporting hierarchy nodes link to canonical concepts only by exact code.
SELECT concept.lookup_code, node.ordinal, node.source_text
FROM json_each(:codes) AS code
CROSS JOIN concept
  ON concept.source_release_id = :release_id AND concept.lookup_code = code.value
CROSS JOIN reference_node AS node
  ON node.source_release_id = :release_id AND node.ordinal = concept.node_ordinal
LIMIT :row_limit;
