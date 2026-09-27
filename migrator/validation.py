from .change_safety import is_safe_change
from .db import Database
from .models import (
    ColumnChange,
    MigrationValidation,
    ValidationIssue,
)


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


def validate_migration(
    db: Database,
    root: str,
    removed_columns: set[str],
    changed_columns: set[str],
    changes: list[ColumnChange] | None = None,
) -> MigrationValidation:
    validation = MigrationValidation()

    if changes:
        for change in changes:
            if not is_safe_change(change):
                validation.issues.append(
                    ValidationIssue(
                        object_name=root,
                        object_type="ROOT",
                        column_name=change.column_name,
                        issue_type=(
                            change.change_reason
                            or change.change_type
                        ),
                    )
                )

    rows = db.query(
        COLUMN_DEPENDENCY_SQL,
        (root,),
    )

    for (
        dependent_oid,
        dependent_object,
        dependent_type,
        referenced_column,
    ) in rows:
        if referenced_column in removed_columns:
            validation.issues.append(
                ValidationIssue(
                    object_name=dependent_object,
                    object_type=dependent_type,
                    column_name=referenced_column,
                    issue_type="REMOVED_COLUMN",
                )
            )

        elif referenced_column in changed_columns:
            validation.issues.append(
                ValidationIssue(
                    object_name=dependent_object,
                    object_type=dependent_type,
                    column_name=referenced_column,
                    issue_type="CHANGED_COLUMN",
                )
            )

    return validation