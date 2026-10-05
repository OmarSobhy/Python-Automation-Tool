from .models import (
    Column,
    DatabaseObject,
    MigrationPlan,
    SchemaComparison,
)
from .sql_utils import quote_identifier, qualified_name


def _is_physical_table(
    obj: DatabaseObject,
) -> bool:
    return obj.object_type in {
        "TABLE",
        "PARTITIONED TABLE",
    }


def generate_drop_sql(
    drop_order: list[int],
    objects: dict[int, DatabaseObject],
) -> str:
    statements = []

    for oid in drop_order:
        if oid not in objects:
            continue

        obj = objects[oid]

        # Physical tables are managed externally and must never be
        # dropped or recreated by the migration tool.
        if _is_physical_table(obj):
            continue

        object_name = qualified_name(
            obj.schema,
            obj.name,
        )

        if obj.object_type == "VIEW":
            statements.append(
                f"DROP VIEW {object_name};"
            )

        elif obj.object_type == "MATERIALIZED VIEW":
            statements.append(
                f"DROP MATERIALIZED VIEW {object_name};"
            )

        else:
            raise ValueError(
                f"Unsupported object type: {obj.object_type}"
            )

    return "\n".join(statements)


def _generate_table_create_sql(
    obj: DatabaseObject,
    columns: list[Column],
) -> str:
    object_name = qualified_name(
        obj.schema,
        obj.name,
    )

    if not columns:
        raise ValueError(
            f"Table has no captured columns: {object_name}"
        )

    select_columns = []

    for column in columns:
        select_columns.append(
            f"NULL::{column.data_type} "
            f"AS {quote_identifier(column.name)}"
        )

    return (
        f"CREATE TABLE {object_name} AS\n"
        "SELECT\n"
        + ",\n".join(
            f"    {column}"
            for column in select_columns
        )
        + "\nWITH NO DATA;"
    )


def generate_object_create_sql(
    obj: DatabaseObject,
    definition: str | None,
    proposed_columns: list[Column] | None = None,
) -> str:
    object_name = qualified_name(
        obj.schema,
        obj.name,
    )

    if obj.object_type == "TABLE":
        columns = proposed_columns or obj.columns

        return _generate_table_create_sql(
            obj,
            columns,
        )

    if not definition:
        raise ValueError(
            f"Object has no definition: {object_name}"
        )

    definition = definition.rstrip().rstrip(";")

    if obj.object_type == "VIEW":
        return (
            f"CREATE VIEW {object_name} AS\n"
            f"{definition};"
        )

    if obj.object_type == "MATERIALIZED VIEW":
        sql = (
            f"CREATE MATERIALIZED VIEW "
            f"{object_name} AS\n{definition}"
        )

        if obj.materialized_view_populated is False:
            sql += " WITH NO DATA"

        return sql + ";"

    raise ValueError(
        f"Unsupported object type: {obj.object_type}"
    )


def generate_create_sql(
    create_order: list[int],
    objects: dict[int, DatabaseObject],
    root_oid: int | None = None,
    proposed_definition: str | None = None,
    proposed_columns: list[Column] | None = None,
) -> str:
    statements = []

    for oid in create_order:
        if oid not in objects:
            continue

        obj = objects[oid]

        # Physical tables are managed externally and must never be
        # created or otherwise modified by the migration tool.
        if _is_physical_table(obj):
            continue

        definition = obj.definition
        columns = None

        if (
            root_oid is not None
            and oid == root_oid
        ):
            if proposed_definition is not None:
                definition = proposed_definition

            columns = proposed_columns

        if not definition:
            object_name = qualified_name(
                obj.schema,
                obj.name,
            )

            raise ValueError(
                f"Object has no definition: {object_name}"
            )

        statements.append(
            generate_object_create_sql(
                obj,
                definition,
                proposed_columns=columns,
            )
        )

        if obj.comment is not None:
            object_name = qualified_name(
                obj.schema,
                obj.name,
            )

            escaped_comment = obj.comment.replace(
                "'",
                "''",
            )

            statements.append(
                f"COMMENT ON {obj.object_type} "
                f"{object_name} "
                f"IS '{escaped_comment}';"
            )

        for index in obj.indexes:
            statements.append(
                f"{index.definition};"
            )

    return "\n".join(statements)


def generate_table_alter_sql(
    obj: DatabaseObject,
    comparison: SchemaComparison,
) -> str:
    """
    Generate in-place ALTER TABLE statements for physical tables.

    Supported:
      - ADD COLUMN
      - DROP COLUMN

    Not currently supported:
      - changing an existing column's data type
      - changing an existing column's nullability

    DROP COLUMN intentionally does NOT use CASCADE.

    The migration executor drops known dependent views/materialized
    views explicitly before this function runs, then recreates them
    afterward. This keeps dependency management under the control of
    the migration planner instead of PostgreSQL's implicit CASCADE
    behavior.
    """

    if obj.object_type not in {
        "TABLE",
        "PARTITIONED TABLE",
    }:
        return ""

    statements = []

    object_name = qualified_name(
        obj.schema,
        obj.name,
    )

    # --------------------------------------------------------------
    # DROP COLUMNS
    #
    # Dependencies have already been dropped by the executor.
    # Therefore DROP COLUMN can safely run without CASCADE.
    # --------------------------------------------------------------
    for change in comparison.removed:
        statements.append(
            f"ALTER TABLE {object_name}\n"
            f"DROP COLUMN {quote_identifier(change.column_name)};"
        )

    # --------------------------------------------------------------
    # ADD COLUMNS
    # --------------------------------------------------------------
    for change in comparison.added:
        column = change.new_column

        if column is None:
            continue

        if not column.nullable:
            raise ValueError(
                "Cannot automatically add a NOT NULL column without "
                "a default value to a physical table: "
                f"{obj.schema}.{obj.name}.{column.name}"
            )

        statements.append(
            f"ALTER TABLE {object_name}\n"
            f"ADD COLUMN {quote_identifier(column.name)} "
            f"{column.data_type};"
        )

    # --------------------------------------------------------------
    # TYPE / NULLABILITY CHANGES
    #
    # These remain intentionally unsupported because they require
    # explicit conversion/default semantics.
    # --------------------------------------------------------------
    if comparison.changed:
        changed = ", ".join(
            change.column_name
            for change in comparison.changed
        )

        raise ValueError(
            "Automatic column type/nullability changes are not "
            "supported yet for "
            f"{object_name}: {changed}"
        )

    return "\n".join(statements)


def generate_migration_sql(
    drop_order: list[int],
    create_order: list[int],
    objects: dict[int, DatabaseObject],
    root_oid: int | None = None,
    proposed_definition: str | None = None,
    proposed_columns: list[Column] | None = None,
    comparison: SchemaComparison | None = None,
) -> str:
    drop_sql = generate_drop_sql(
        drop_order,
        objects,
    )

    alter_sql = ""

    if (
        root_oid is not None
        and comparison is not None
        and root_oid in objects
    ):
        root_object = objects[root_oid]

        if _is_physical_table(root_object):
            alter_sql = generate_table_alter_sql(
                root_object,
                comparison,
            )

    create_sql = generate_create_sql(
        create_order,
        objects,
        root_oid=root_oid,
        proposed_definition=proposed_definition,
        proposed_columns=proposed_columns,
    )

    sections = []

    if drop_sql:
        sections.append(
            "-- DROP OBJECTS\n"
            + drop_sql
        )

    if alter_sql:
        sections.append(
            "-- ALTER TABLES\n"
            + alter_sql
        )

    if create_sql:
        sections.append(
            "-- CREATE OBJECTS\n"
            + create_sql
        )

    return "\n\n".join(sections)


def generate_family_create_sql(
    create_order: list[int],
    objects: dict[int, DatabaseObject],
    proposed_definitions: dict[int, str],
    proposed_columns: dict[int, list[Column]] | None = None,
) -> str:
    statements = []

    proposed_columns = proposed_columns or {}

    for oid in create_order:
        if oid not in objects:
            continue

        obj = objects[oid]

        # Physical tables are managed externally and must never be
        # created or otherwise modified by the migration tool.
        if _is_physical_table(obj):
            continue

        definition = proposed_definitions.get(
            oid,
            obj.definition,
        )

        columns = proposed_columns.get(oid)

        if not definition:
            object_name = qualified_name(
                obj.schema,
                obj.name,
            )

            raise ValueError(
                f"Object has no definition: {object_name}"
            )

        statements.append(
            generate_object_create_sql(
                obj,
                definition,
                proposed_columns=columns,
            )
        )

        if obj.comment is not None:
            object_name = qualified_name(
                obj.schema,
                obj.name,
            )

            escaped_comment = obj.comment.replace(
                "'",
                "''",
            )

            statements.append(
                f"COMMENT ON {obj.object_type} "
                f"{object_name} "
                f"IS '{escaped_comment}';"
            )

        for index in obj.indexes:
            statements.append(
                f"{index.definition};"
            )

    return "\n".join(statements)


def generate_family_migration_sql(
    plan: MigrationPlan,
) -> str:
    drop_sql = generate_drop_sql(
        plan.drop_order,
        plan.objects,
    )

    proposed_columns = getattr(
        plan,
        "proposed_columns",
        {},
    )

    create_sql = generate_family_create_sql(
        plan.create_order,
        plan.objects,
        plan.proposed_definitions,
        proposed_columns=proposed_columns,
    )

    sections = []

    if drop_sql:
        sections.append(
            "-- DROP OBJECTS\n"
            + drop_sql
        )

    if create_sql:
        sections.append(
            "-- CREATE OBJECTS\n"
            + create_sql
        )

    return "\n\n".join(sections)