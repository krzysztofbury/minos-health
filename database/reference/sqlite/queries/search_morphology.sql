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
), candidates(ordinal, document_id, title_rank, prefix_rank) AS (
    SELECT coalesce(owner.concept_ordinal, document.node_ordinal), document.id,
           CASE WHEN document.description_type <> 'context'
                AND document.node_ordinal = coalesce(owner.concept_ordinal, document.node_ordinal)
           THEN 0 ELSE 1 END,
           CASE WHEN substr(document.normalized_text, 1, length(:text)) = :text THEN 0 ELSE 1 END
    FROM matching CROSS JOIN search_document AS document ON document.id = matching.document_id
    LEFT JOIN search_owner AS owner ON owner.source_release_id = document.source_release_id AND owner.document_id = document.id
    WHERE document.source_release_id = :release_id
), ranked AS (
    SELECT candidates.ordinal, candidates.document_id, candidates.title_rank,
           candidates.prefix_rank,
            CASE WHEN :supplementary_prefix <> ''
                  AND substr(concept.lookup_code, 1, 1) = :supplementary_prefix
            THEN 1 ELSE 0 END AS supplementary,
            coalesce(length(replace(concept.lookup_code, '.', '')), 99) AS code_depth,
            coalesce(json_extract(:jgp_counts, '$."' || concept.lookup_code || '"'), 0) AS jgp_count,
           row_number() OVER (
               PARTITION BY candidates.ordinal
               ORDER BY candidates.title_rank, candidates.prefix_rank, candidates.document_id
           ) AS position
    FROM candidates
    LEFT JOIN concept
      ON concept.source_release_id = :release_id AND concept.node_ordinal = candidates.ordinal
    WHERE candidates.ordinal NOT IN (SELECT value FROM json_each(:excluded))
)
-- Lexical and structural, not clinical: a code's own title before inclusion
-- text, a title that begins with the query before one that only contains it,
-- a broader code before its narrower codes, then source order.
SELECT ordinal, document_id FROM ranked WHERE position = 1
ORDER BY supplementary, title_rank, prefix_rank, code_depth, jgp_count DESC, ordinal LIMIT :row_limit;
