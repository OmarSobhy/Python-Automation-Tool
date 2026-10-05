from .change_safety import is_safe_change
from .db import Database
from .models import (
    ColumnChange,
    MigrationValidation,
    ValidationIssue,
)


def validate_migration(
    db: Database,
    root: str,
    removed_columns: set[str],
    changed_columns: set[str],
    changes: list[ColumnChange] | None = None,
) -> MigrationValidation:
    validation = MigrationValidation()

    # Keep rejecting schema changes that are explicitly classified as
    # unsafe by change_safety.py.
    #
    # A removed column itself is not automatically unsafe here.
    # Dependents will be dropped before the ALTER TABLE and recreated
    # afterward inside the same transaction. If a dependent can no
    # longer be recreated, PostgreSQL will raise an error and the
    # migration will roll back.
    if changes:
        for change in changes:
            if change.change_type == "REMOVED":
                continue

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

    return validation