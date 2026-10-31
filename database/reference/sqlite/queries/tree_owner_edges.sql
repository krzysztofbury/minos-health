-- One level of a tree listing. A single table on purpose: see store/hierarchy_walk.py.
SELECT parent_ordinal, child_ordinal FROM reference_relation
WHERE source_release_id = :release_id
  AND parent_ordinal IN (SELECT value FROM json_each(:ordinals))
  AND relation_type = 'contains';
