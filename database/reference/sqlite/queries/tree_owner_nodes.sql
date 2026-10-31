SELECT ordinal, kind, source_text FROM reference_node
WHERE source_release_id = :release_id
  AND ordinal IN (SELECT value FROM json_each(:ordinals));
