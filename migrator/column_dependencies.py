from .db import Database


COLUMN_DEPENDENCY_SQL = """
SELECT DISTINCT
    dependent.oid AS dependent_oid,
    dependent.oid::regclass::text AS dependent_object,
    referenced.oid AS referenced_oid,
    referenced.oid::regclass::text AS referenced_object,
    referenced_attr.attname AS referenced_column
FROM pg_depend d
JOIN pg_rewrite rw
    ON rw.oid = d.objid
JOIN pg_class dependent
    ON dependent.oid = rw.ev_class
JOIN pg_class referenced
    ON referenced.oid = d.refobjid
JOIN pg_attribute referenced_attr
    ON referenced_attr.attrelid = referenced.oid
   AND referenced_attr.attnum = d.refobjsubid
WHERE referenced.oid = %s::regclass
  AND dependent.relkind IN ('v', 'm')
  AND d.refobjsubid > 0
ORDER BY
    dependent_object,
    referenced_column;
"""


def get_direct_column_dependencies(
    db: Database,
    object_name: str,
):
    rows = db.query(
        COLUMN_DEPENDENCY_SQL,
        (object_name,),
    )

    return rows