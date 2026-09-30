SELECT document.node_ordinal, min(document.id)
FROM search_document AS document
WHERE document.source_release_id = :release_id AND document.language = :language
  AND document.normalized_text = :literal_text AND document.description_type <> 'context'
  AND document.node_ordinal NOT IN (SELECT value FROM json_each(:excluded))
GROUP BY document.node_ordinal ORDER BY document.node_ordinal LIMIT :row_limit;
