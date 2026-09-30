SELECT document.node_ordinal, document.language, document.description_type,
       coalesce(description.source_text, attribute.source_text, node.source_text)
FROM search_document AS document
JOIN reference_node AS node ON node.source_release_id = document.source_release_id AND node.ordinal = document.node_ordinal
LEFT JOIN concept_description AS description ON description.id = document.description_id
LEFT JOIN reference_node_attribute AS attribute ON attribute.source_release_id = document.source_release_id
    AND attribute.node_ordinal = document.node_ordinal AND attribute.name = document.attribute_name
WHERE document.source_release_id = :release_id AND document.id = :document_id;
