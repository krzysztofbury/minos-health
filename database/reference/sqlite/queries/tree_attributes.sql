SELECT attribute.node_ordinal, attribute.name, attribute.source_text
FROM json_each(:ordinals) AS item
CROSS JOIN reference_node_attribute AS attribute
  ON attribute.source_release_id = :release_id AND attribute.node_ordinal = item.value
ORDER BY attribute.node_ordinal, attribute.source_order
LIMIT :row_limit;
