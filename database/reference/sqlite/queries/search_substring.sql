SELECT coalesce(owner.concept_ordinal, document.node_ordinal) AS ordinal, min(document.id)
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
GROUP BY ordinal ORDER BY ordinal LIMIT :row_limit;
