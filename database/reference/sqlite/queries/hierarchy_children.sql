-- One level down. A single table on purpose: see store/hierarchy_walk.py.
SELECT DISTINCT child_ordinal FROM reference_relation
WHERE source_release_id = :release_id
  AND parent_ordinal IN (SELECT value FROM json_each(:ordinals))
  AND relation_type = :relation_type;
