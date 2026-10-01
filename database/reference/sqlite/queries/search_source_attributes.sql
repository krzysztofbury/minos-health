SELECT name, source_text
FROM reference_node_attribute
WHERE source_release_id = :release_id AND node_ordinal = :ordinal
ORDER BY source_order
LIMIT 17;
