WITH catalog AS (
    SELECT concept.lookup_code, node.source_text, node.ordinal
    FROM concept JOIN reference_node AS node
      ON node.source_release_id = concept.source_release_id AND node.ordinal = concept.node_ordinal
    WHERE concept.source_release_id = :catalog
), workbook AS (
    SELECT concept.lookup_code, node.source_text
    FROM concept JOIN reference_node AS node
      ON node.source_release_id = concept.source_release_id AND node.ordinal = concept.node_ordinal
    WHERE concept.source_release_id = :workbook
)
SELECT
    (SELECT count(*) FROM catalog LEFT JOIN workbook USING (lookup_code) WHERE workbook.lookup_code IS NULL),
    (SELECT count(*) FROM workbook LEFT JOIN catalog USING (lookup_code) WHERE catalog.lookup_code IS NULL),
    (SELECT count(*) FROM catalog JOIN workbook USING (lookup_code) WHERE catalog.source_text <> workbook.source_text),
    (SELECT count(*) FROM reference_node_attribute WHERE source_release_id = :workbook
        AND name = 'evidence_kind' AND source_text = 'conflicting_title_or_code_form'),
    (SELECT count(*) FROM catalog LEFT JOIN workbook USING (lookup_code)
        JOIN reference_node_attribute AS attribute ON attribute.source_release_id = :catalog
            AND attribute.node_ordinal = catalog.ordinal AND attribute.name = 'status'
        WHERE workbook.lookup_code IS NULL AND attribute.source_text = 'A');
