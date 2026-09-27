from .db import Database
from .models import Column, DatabaseObject, Index


OBJECT_SQL = """
SELECT
    c.oid,
    n.nspname AS schema_name,
    c.relname AS object_name,
    CASE c.relkind
        WHEN 'r' THEN 'TABLE'
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW'
    END AS object_type,
    CASE c.relkind
        WHEN 'v' THEN pg_get_viewdef(c.oid, true)
        WHEN 'm' THEN pg_get_viewdef(c.oid, true)
        ELSE NULL
    END AS definition,
    pg_get_userbyid(c.relowner) AS owner,
    obj_description(c.oid, 'pg_class') AS comment,
    CASE
        WHEN c.relkind = 'm'
        THEN c.relispopulated
        ELSE NULL
    END AS materialized_view_populated
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE c.oid = %s
  AND c.relkind IN ('r', 'v', 'm');
"""


COLUMN_SQL = """
SELECT
    a.attnum AS column_number,
    a.attname AS column_name,
    pg_catalog.format_type(
        a.atttypid,
        a.atttypmod
    ) AS data_type,
    NOT a.attnotnull AS nullable
FROM pg_attribute a
WHERE a.attrelid = %s
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum;
"""


INDEX_SQL = """
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = %s
  AND tablename = %s
ORDER BY indexname;
"""


def get_object(
    db: Database,
    oid: int,
) -> DatabaseObject:
    rows = db.query(
        OBJECT_SQL,
        (oid,),
    )

    if not rows:
        raise ValueError(
            f"Object with OID {oid} was not found"
        )

    (
        object_oid,
        schema_name,
        object_name,
        object_type,
        definition,
        owner,
        comment,
        materialized_view_populated,
    ) = rows[0]

    columns = []

    for (
        position,
        name,
        data_type,
        nullable,
    ) in db.query(
        COLUMN_SQL,
        (oid,),
    ):
        columns.append(
            Column(
                name=name,
                data_type=data_type,
                nullable=nullable,
                position=position,
            )
        )

    indexes = []

    for (
        index_name,
        index_definition,
    ) in db.query(
        INDEX_SQL,
        (schema_name, object_name),
    ):
        indexes.append(
            Index(
                name=index_name,
                definition=index_definition,
            )
        )

    return DatabaseObject(
        oid=object_oid,
        schema=schema_name,
        name=object_name,
        object_type=object_type,
        definition=definition,
        owner=owner,
        comment=comment,
        materialized_view_populated=(
            materialized_view_populated
        ),
        columns=columns,
        indexes=indexes,
    )