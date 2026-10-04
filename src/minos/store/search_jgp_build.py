"""Explicit attachment 9 membership projection; no family or prefix expansion."""

import sqlite3


def build_search_jgp(connection: sqlite3.Connection, sql: str) -> None:
    _ = connection.execute("INSERT INTO search_jgp_member " + sql)
    _ = connection.execute("""INSERT INTO search_jgp_count
        SELECT release_id,catalog,system,code,count(DISTINCT group_code)
        FROM search_jgp_member GROUP BY release_id,catalog,system,code""")
