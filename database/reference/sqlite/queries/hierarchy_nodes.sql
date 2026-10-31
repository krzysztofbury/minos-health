SELECT ordinal, source_code, source_text, kind FROM reference_node
WHERE source_release_id = :release_id
  AND ordinal IN (SELECT value FROM json_each(:ordinals))
ORDER BY ordinal
LIMIT :row_limit;
