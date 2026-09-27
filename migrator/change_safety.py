from .models import ColumnChange


SAFE_TYPE_CHANGES = {
    ("smallint", "integer"),
    ("smallint", "bigint"),
    ("integer", "bigint"),
}


def is_safe_change(change: ColumnChange) -> bool:
    if change.change_type == "ADDED":
        return True

    if change.change_type == "REMOVED":
        return False

    if change.change_type != "CHANGED":
        return False

    if change.old_column is None:
        return False

    if change.new_column is None:
        return False

    old_type = change.old_column.data_type
    new_type = change.new_column.data_type

    if old_type != new_type:
        return (
            old_type,
            new_type,
        ) in SAFE_TYPE_CHANGES

    if (
        change.old_column.nullable
        and not change.new_column.nullable
    ):
        return False

    return True

