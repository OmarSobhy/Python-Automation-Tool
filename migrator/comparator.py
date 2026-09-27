from .models import Column, ColumnChange, SchemaComparison


def compare_columns(
    old_columns: list[Column],
    new_columns: list[Column],
) -> SchemaComparison:
    old_by_name = {
        column.name: column
        for column in old_columns
    }

    new_by_name = {
        column.name: column
        for column in new_columns
    }

    comparison = SchemaComparison()

    for name, new_column in new_by_name.items():
        if name not in old_by_name:
            comparison.added.append(
                ColumnChange(
                    change_type="ADDED",
                    column_name=name,
                    new_column=new_column,
                )
            )

    for name, old_column in old_by_name.items():
        if name not in new_by_name:
            comparison.removed.append(
                ColumnChange(
                    change_type="REMOVED",
                    column_name=name,
                    old_column=old_column,
                )
            )

    for name in old_by_name.keys() & new_by_name.keys():
        old_column = old_by_name[name]
        new_column = new_by_name[name]

        type_changed = (
            old_column.data_type
            != new_column.data_type
        )

        nullability_changed = (
            old_column.nullable
            != new_column.nullable
        )

        if type_changed and nullability_changed:
            change_reason = (
                "TYPE_AND_NULLABILITY_CHANGED"
            )

        elif type_changed:
            change_reason = "TYPE_CHANGED"

        elif nullability_changed:
            change_reason = "NULLABILITY_CHANGED"

        else:
            continue

        comparison.changed.append(
            ColumnChange(
                change_type="CHANGED",
                column_name=name,
                old_column=old_column,
                new_column=new_column,
                change_reason=change_reason,
            )
        )

    return comparison
