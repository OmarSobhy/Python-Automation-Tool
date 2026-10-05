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


DIRECT_DEPENDENCY_SQL = """
SELECT DISTINCT
    dependent.oid AS dependent_oid,
    dependent.oid::regclass::text AS dependent_object,
    CASE dependent.relkind
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW'
    END AS dependent_type
FROM pg_depend d
JOIN pg_rewrite rw
    ON rw.oid = d.objid
JOIN pg_class dependent
    ON dependent.oid = rw.ev_class
WHERE d.refobjid = %s::regclass
  AND dependent.relkind IN ('v', 'm')
ORDER BY
    dependent_object;
"""


def get_direct_impact(
    db: Database,
    object_name: str,
    changed_columns: set[str],
) -> list[ImpactedObject]:
    """
    Return direct dependent views/materialized views.

    If changed_columns is non-empty, column-level dependency information
    is used to identify the columns directly affected.

    If changed_columns is empty, all direct dependent views/materialized
    views are returned. This is important for migrations such as ADD
    COLUMN, where there may be no existing column dependency but the
    dependent objects still need to be considered by the migration
    planner.
    """

    # -------------------------------------------------------------
    # If there are known removed/changed columns, preserve the
    # column-level dependency behavior.
    # -------------------------------------------------------------
    if changed_columns:
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

            if (
                referenced_column
                not in impacted[dependent_oid].columns
            ):
                impacted[dependent_oid].columns.append(
                    referenced_column
                )

        return list(impacted.values())

    # -------------------------------------------------------------
    # No removed/changed columns.
    #
    # This covers ADD COLUMN and also the case where analysis is run
    # after the proposed schema has already been applied.
    #
    # In this situation, return all direct dependent views/MVs.
    # -------------------------------------------------------------
    rows = db.query(
        DIRECT_DEPENDENCY_SQL,
        (object_name,),
    )

    return [
        ImpactedObject(
            oid=dependent_oid,
            object_name=dependent_object,
            object_type=dependent_type,
        )
        for (
            dependent_oid,
            dependent_object,
            dependent_type,
        ) in rows
    ]


def get_transitive_impact(
    db: Database,
    root: str,
    changed_columns: set[str],
) -> list[ImpactedObject]:
    """
    Return all downstream views/materialized views affected by the
    migration root.

    The first level may use column-level dependency information when
    removed/changed columns are known.

    Once a dependent object is impacted, all of its downstream
    dependent views/materialized views are included as well.
    """

    direct = get_direct_impact(
        db,
        root,
        changed_columns,
    )

    impacted = {
        item.oid: item
        for item in direct
    }

    # -------------------------------------------------------------
    # Continue walking through every object that is already impacted.
    # -------------------------------------------------------------
    index = 0

    while index < len(impacted):
        current = list(impacted.values())[index]

        rows = db.query(
            DIRECT_DEPENDENCY_SQL.replace(
                "%s::regclass",
                "%s",
            ),
            (current.oid,),
        )

        for (
            dependent_oid,
            dependent_object,
            dependent_type,
        ) in rows:

            if dependent_oid in impacted:
                continue

            impacted[dependent_oid] = ImpactedObject(
                oid=dependent_oid,
                object_name=dependent_object,
                object_type=dependent_type,
            )

        index += 1

    return list(impacted.values())
