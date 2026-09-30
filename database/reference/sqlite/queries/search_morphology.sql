WITH matching(document_id) AS (
    SELECT token.document_id
    FROM json_each(:variants) AS requested
    CROSS JOIN json_each(requested.value) AS alternative
    CROSS JOIN search_word_lemma AS mapping ON mapping.source_release_id = :release_id
      AND mapping.language = :language AND mapping.lemma = alternative.value
    CROSS JOIN search_token AS token ON token.source_release_id = mapping.source_release_id
      AND token.language = mapping.language AND token.word = mapping.word
    GROUP BY token.document_id
    HAVING count(DISTINCT requested.key) = json_array_length(:variants)
)
SELECT coalesce(owner.concept_ordinal, document.node_ordinal) AS ordinal, min(document.id)
FROM matching CROSS JOIN search_document AS document ON document.id = matching.document_id
LEFT JOIN search_owner AS owner ON owner.source_release_id = document.source_release_id AND owner.document_id = document.id
WHERE document.source_release_id = :release_id
  AND coalesce(owner.concept_ordinal, document.node_ordinal) NOT IN (SELECT value FROM json_each(:excluded))
GROUP BY ordinal ORDER BY ordinal LIMIT :row_limit;
