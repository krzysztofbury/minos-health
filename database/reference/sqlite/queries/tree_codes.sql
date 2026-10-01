SELECT item.value, concept.lookup_code
FROM json_each(:ordinals) AS item
CROSS JOIN concept
  ON concept.source_release_id = :release_id AND concept.node_ordinal = item.value;
