from .models import Column
from .comparator import compare_columns


def test_comparator():
    old_columns = [
        Column("id", "bigint", True, 1),
        Column("name", "text", True, 2),
        Column("amount", "numeric", True, 3),
        Column("last_order_at", "timestamp with time zone", True, 4),
    ]

    new_columns = [
        Column("id", "bigint", True, 1),
        Column("name", "text", True, 2),
        Column("total_amount", "numeric", True, 3),
        Column("currency", "text", True, 4),
    ]

    comparison = compare_columns(
        old_columns,
        new_columns,
    )

    print("ADDED:")

    for change in comparison.added:
        print(f"  + {change.column_name}")

    print("REMOVED:")

    for change in comparison.removed:
        print(f"  - {change.column_name}")

    print("CHANGED:")

    for change in comparison.changed:
        print(f"  ~ {change.column_name}")

    print()
    print(f"BREAKING: {comparison.is_breaking}")