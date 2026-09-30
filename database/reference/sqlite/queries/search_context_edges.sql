SELECT parent_ordinal, child_ordinal FROM reference_relation
WHERE source_release_id = :release_id AND relation_type = 'contains'
  AND parent_ordinal IN (SELECT value FROM json_each(:ordinals))
  AND child_ordinal IN (SELECT value FROM json_each(:ordinals))
ORDER BY parent_ordinal, child_ordinal LIMIT 1025;
