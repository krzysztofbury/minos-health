-- One level up. A single table on purpose: see store/hierarchy_walk.py.
SELECT DISTINCT parent_ordinal FROM reference_relation
WHERE source_release_id = :release_id
  AND child_ordinal IN (SELECT value FROM json_each(:ordinals))
  AND relation_type = :relation_type;
