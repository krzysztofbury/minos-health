-- One query per hierarchy level; the relation primary key serves each parent probe.
SELECT relation.parent_ordinal, node.ordinal, node.kind, node.source_code,
       node.source_text, concept.lookup_code
FROM json_each(:parents) AS parent
CROSS JOIN reference_relation AS relation
  ON relation.source_release_id = :release_id
 AND relation.parent_ordinal = parent.value
 AND relation.relation_type = 'contains'
CROSS JOIN reference_node AS node
  ON node.source_release_id = :release_id AND node.ordinal = relation.child_ordinal
LEFT JOIN concept
  ON concept.source_release_id = :release_id AND concept.node_ordinal = node.ordinal
ORDER BY node.ordinal
LIMIT :row_limit;
