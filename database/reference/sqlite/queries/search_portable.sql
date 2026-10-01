WITH matching(document_id) AS (
    SELECT token.document_id
    FROM json_each(:ranges) AS requested CROSS JOIN search_token AS token
    WHERE NOT :code_like AND token.source_release_id = :release_id AND token.language = :language
      AND token.word >= json_extract(requested.value, '$.lower')
      AND token.word < json_extract(requested.value, '$.upper')
      AND (json_extract(requested.value, '$.prefix') OR token.word = json_extract(requested.value, '$.lower'))
    GROUP BY token.document_id
    HAVING count(DISTINCT requested.key) = json_array_length(:ranges)
), candidates(ordinal, document_id) AS (
    SELECT coalesce(owner.concept_ordinal, document.node_ordinal), document.id
    FROM matching CROSS JOIN search_document AS document ON document.id = matching.document_id
    LEFT JOIN search_owner AS owner ON owner.source_release_id = document.source_release_id AND owner.document_id = document.id
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
