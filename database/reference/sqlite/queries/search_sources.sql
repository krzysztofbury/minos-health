SELECT source_release_id, node_ordinal, language, description_type,
       id AS description_id, NULL AS attribute_name, source_text
FROM concept_description
UNION ALL
SELECT source_release_id, ordinal, 'pl', 'context', NULL, NULL, source_text
FROM reference_node WHERE kind = 'text' AND length(trim(source_text)) > 0
UNION ALL
SELECT attribute.source_release_id, attribute.node_ordinal, 'en', 'context',
       NULL, attribute.name, attribute.source_text
FROM reference_node_attribute AS attribute JOIN reference_node AS node
  ON node.source_release_id = attribute.source_release_id AND node.ordinal = attribute.node_ordinal
WHERE node.kind = 'text' AND attribute.name = 'EN' AND length(trim(attribute.source_text)) > 0
ORDER BY source_release_id, node_ordinal, language, description_type;
