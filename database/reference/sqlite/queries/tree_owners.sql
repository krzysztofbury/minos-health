-- Listed nodes with a structural descendant through anonymous containers. A
-- fixed row cap would drop the last owners of a long listing (ICD-11 blocks).
WITH RECURSIVE walk(owner, ordinal) AS (
    SELECT parent.value, relation.child_ordinal
    FROM json_each(:parents) AS parent
    CROSS JOIN reference_relation AS relation
      ON relation.source_release_id = :release_id
     AND relation.parent_ordinal = parent.value
     AND relation.relation_type = 'contains'
    UNION
    SELECT walk.owner, relation.child_ordinal
    FROM walk
    CROSS JOIN reference_node AS node
      ON node.source_release_id = :release_id
     AND node.ordinal = walk.ordinal
     AND node.kind = 'container'
    CROSS JOIN reference_relation AS relation
      ON relation.source_release_id = :release_id
     AND relation.parent_ordinal = walk.ordinal
     AND relation.relation_type = 'contains'
)
SELECT DISTINCT walk.owner
FROM walk CROSS JOIN reference_node AS node
  ON node.source_release_id = :release_id
 AND node.ordinal = walk.ordinal
 AND node.kind IN ('classification', 'range');
