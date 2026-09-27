from .db import Database
from .models import Dependency


DEPENDENCY_SQL = """
WITH RECURSIVE dependency_tree AS (
    SELECT
        c.oid,
        0 AS depth,
        n.nspname || '.' || c.relname AS object_name,
        c.relkind,
        ARRAY[c.oid]::oid[] AS path
    FROM pg_class c
    JOIN pg_namespace n
        ON n.oid = c.relnamespace
    WHERE c.oid = %s::regclass

    UNION ALL

    SELECT
        dependent.oid,
        dt.depth + 1,
        dependent_ns.nspname || '.' || dependent.relname,
        dependent.relkind,
        dt.path || dependent.oid
    FROM dependency_tree dt
    JOIN pg_depend d
        ON d.refobjid = dt.oid
    JOIN pg_rewrite rw
        ON rw.oid = d.objid
    JOIN pg_class dependent
        ON dependent.oid = rw.ev_class
    JOIN pg_namespace dependent_ns
        ON dependent_ns.oid = dependent.relnamespace
    WHERE dependent.relkind IN ('v', 'm')
      AND dependent.oid <> dt.oid
      AND NOT dependent.oid = ANY(dt.path)
)
SELECT DISTINCT
    oid,
    depth,
    object_name,
    relkind
FROM dependency_tree
ORDER BY depth, oid;
"""


EDGE_SQL = """
SELECT DISTINCT
    d.refobjid AS referenced_oid,
    rw.ev_class AS dependent_oid
FROM pg_depend d
JOIN pg_rewrite rw
    ON rw.oid = d.objid
JOIN pg_class dependent
    ON dependent.oid = rw.ev_class
WHERE d.refobjid = ANY(%s::oid[])
  AND rw.ev_class = ANY(%s::oid[])
  AND d.refobjid <> rw.ev_class
  AND dependent.relkind IN ('v', 'm');
"""


def get_dependency_tree(
    db: Database,
    root: str,
):
    return db.query(
        DEPENDENCY_SQL,
        (root,),
    )


def get_dependency_edges(
    db: Database,
    object_oids: set[int] | list[int],
) -> list[Dependency]:
    if not object_oids:
        return []

    oid_list = list(object_oids)

    rows = db.query(
        EDGE_SQL,
        (
            oid_list,
            oid_list,
        ),
    )

    object_oid_set = set(oid_list)

    return [
        Dependency(
            referenced_oid=referenced_oid,
            dependent_oid=dependent_oid,
        )
        for referenced_oid, dependent_oid in rows
        if (
            referenced_oid != dependent_oid
            and referenced_oid in object_oid_set
            and dependent_oid in object_oid_set
        )
    ]