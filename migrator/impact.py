from .db import Database
from .models import ImpactedObject


COLUMN_DEPENDENCY_SQL = """
SELECT DISTINCT
    dependent.oid AS dependent_oid,
    dependent.oid::regclass::text AS dependent_object,
    CASE dependent.relkind
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW'
    END AS dependent_type,
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


def get_direct_impact(
    db: Database,
    object_name: str,
    changed_columns: set[str],
) -> list[ImpactedObject]:

    rows = db.query(
        COLUMN_DEPENDENCY_SQL,
        (object_name,),
    )

    impacted = {}

    for (
        dependent_oid,
        dependent_object,
        dependent_type,
        referenced_column,
    ) in rows:

        if referenced_column not in changed_columns:
            continue

        if dependent_oid not in impacted:
            impacted[dependent_oid] = ImpactedObject(
                oid=dependent_oid,
                object_name=dependent_object,
                object_type=dependent_type,
            )

        impacted[dependent_oid].columns.append(
            referenced_column
        )

    return list(impacted.values())

def get_transitive_impact(
    db: Database,
    root: str,
    changed_columns: set[str],
) -> list[ImpactedObject]:

    direct = get_direct_impact(
        db,
        root,
        changed_columns,
    )

    impacted = {
        item.oid: item
        for item in direct
    }

    # Continue walking through objects that are already impacted.
    index = 0

    while index < len(impacted):
        current = list(impacted.values())[index]

        rows = db.query(
            """
            SELECT DISTINCT
                rw.ev_class AS dependent_oid,
                rw.ev_class::regclass::text AS dependent_object,
                CASE dependent.relkind
                    WHEN 'v' THEN 'VIEW'
                    WHEN 'm' THEN 'MATERIALIZED VIEW'
                END AS dependent_type
            FROM pg_depend d
            JOIN pg_rewrite rw
                ON rw.oid = d.objid
            JOIN pg_class dependent
                ON dependent.oid = rw.ev_class
            WHERE d.refobjid = %s
              AND dependent.relkind IN ('v', 'm')
            """,
            (current.oid,),
        )

        for (
            dependent_oid,
            dependent_object,
            dependent_type,
        ) in rows:

            if dependent_oid not in impacted:
                impacted[dependent_oid] = ImpactedObject(
                    oid=dependent_oid,
                    object_name=dependent_object,
                    object_type=dependent_type,
                )

        index += 1

    return list(impacted.values())