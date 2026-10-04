WITH RECURSIVE seeds(release_id,catalog,group_code,selector_id) AS (
    SELECT release_id,catalog,group_code,json_extract(payload_json,'$.payload.selector_id')
    FROM group_condition WHERE json_type(payload_json,'$.payload.selector_id')='integer'
    UNION
    SELECT g.release_id,g.catalog,g.group_code,j.value FROM group_condition g,
        json_each(g.payload_json,'$.payload.selector_ids') j
    UNION
    SELECT g.release_id,g.catalog,g.group_code,j.value FROM group_condition g,
        json_each(g.payload_json,'$.payload.occurrence_sets') sets,json_each(sets.value) j
), reachable(release_id,catalog,group_code,selector_id,depth) AS (
    SELECT release_id,catalog,group_code,selector_id,0 FROM seeds
    UNION
    SELECT r.release_id,r.catalog,r.group_code,j.value,r.depth+1 FROM reachable r
    JOIN condition_selector s ON s.release_id=r.release_id AND s.catalog=r.catalog AND s.id=r.selector_id,
        json_each(s.payload_json,'$.body.selector_ids') j WHERE r.depth<32
), linked AS (
    SELECT r.release_id,r.catalog,m.system,m.code,r.group_code FROM reachable r
    JOIN condition_selector s ON s.release_id=r.release_id AND s.catalog=r.catalog AND s.id=r.selector_id,
        json_each(s.payload_json,'$.body.list_ids') lists
    JOIN directional_member m ON m.release_id=r.release_id AND m.catalog=r.catalog AND m.list_id=lists.value
    UNION
    SELECT g.release_id,g.catalog,m.system,m.code,g.code FROM directional_member m
    JOIN directional_list l ON l.release_id=m.release_id AND l.catalog=m.catalog AND l.id=m.list_id
    JOIN characteristics_group g ON g.release_id=l.release_id AND g.catalog=l.catalog AND g.code=trim(l.context)
)
SELECT release_id,catalog,system,code,group_code FROM linked;
