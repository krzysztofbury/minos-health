WITH candidates(ordinal, document_id) AS (
    SELECT coalesce(owner.concept_ordinal, document.node_ordinal), document.id
    FROM search_document_fts CROSS JOIN search_document AS document
      ON document.id = search_document_fts.rowid
    LEFT JOIN search_owner AS owner ON owner.source_release_id = document.source_release_id AND owner.document_id = document.id
    WHERE search_document_fts MATCH :match AND NOT :code_like
      AND document.source_release_id = :release_id AND document.language = :language
    UNION ALL
    SELECT concept.node_ordinal,
           (SELECT id FROM search_document WHERE source_release_id = :release_id AND node_ordinal = concept.node_ordinal
            ORDER BY (language = :language) DESC, (description_type = 'preferred') DESC, id LIMIT 1)
    FROM concept WHERE source_release_id = :release_id AND :code_like
      AND lookup_code >= :code_key AND lookup_code < :code_upper
)
SELECT ordinal, min(document_id) FROM candidates
WHERE ordinal NOT IN (SELECT value FROM json_each(:excluded))
GROUP BY ordinal ORDER BY ordinal LIMIT :row_limit;
