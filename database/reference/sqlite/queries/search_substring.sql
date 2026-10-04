WITH candidates(ordinal, document_id, title_rank, prefix_rank) AS (
    SELECT coalesce(owner.concept_ordinal, document.node_ordinal), document.id,
           CASE WHEN document.description_type <> 'context'
                AND document.node_ordinal = coalesce(owner.concept_ordinal, document.node_ordinal)
           THEN 0 ELSE 1 END,
           CASE WHEN substr(document.normalized_text, 1, length(:text)) = :text THEN 0 ELSE 1 END
    FROM search_document AS document
    LEFT JOIN search_owner AS owner ON owner.source_release_id = document.source_release_id AND owner.document_id = document.id
    WHERE document.source_release_id = :release_id AND document.language = :language
      AND instr(document.normalized_text, :text) > 0
      AND NOT EXISTS (
          SELECT 1 FROM json_each(:ranges) AS requested
          WHERE NOT json_extract(requested.value, '$.prefix') AND NOT EXISTS (
              SELECT 1 FROM search_token AS token
              WHERE token.source_release_id = document.source_release_id
                AND token.language = document.language AND token.document_id = document.id
                AND token.word = json_extract(requested.value, '$.lower')
          )
      )
), ranked AS (
    SELECT candidates.ordinal, candidates.document_id, candidates.title_rank,
           candidates.prefix_rank,
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
ORDER BY title_rank, prefix_rank, code_depth, jgp_count DESC, ordinal LIMIT :row_limit;
