SELECT rank, source_title, worksheet, source_row
FROM procedure_rank
WHERE release_id = ? AND grouper_type = 'SZP' AND code = ?;
