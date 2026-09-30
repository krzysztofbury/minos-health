SELECT concept.node_ordinal,
       (SELECT id FROM search_document WHERE source_release_id = :release_id
          AND node_ordinal = concept.node_ordinal
        ORDER BY (language = :language) DESC, (description_type = 'preferred') DESC, id LIMIT 1)
FROM concept WHERE source_release_id = :release_id AND lookup_code = :code_key;
