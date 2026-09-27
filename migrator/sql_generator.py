from .models import Column, DatabaseObject, MigrationPlan
from .sql_utils import quote_identifier, qualified_name


def generate_drop_sql(
    drop_order: list[int],
    objects: dict[int, DatabaseObject],
) -> str:
    statements = []

    for oid in drop_order:
        if oid not in objects:
            continue

        obj = objects[oid]

        object_name = qualified_name(
            obj.schema,
            obj.name,
        )

        if obj.object_type == "TABLE":
            statements.append(
                f"DROP TABLE {object_name};"
            )

        elif obj.object_type == "VIEW":
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
        obj = objects[oid]

        definition = obj.definition
        columns = None

        if (
            root_oid is not None
            and oid == root_oid
        ):
            if proposed_definition is not None:
                definition = proposed_definition

            columns = proposed_columns

        if obj.object_type != "TABLE" and not definition:
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


def generate_migration_sql(
    drop_order: list[int],
    create_order: list[int],
    objects: dict[int, DatabaseObject],
    root_oid: int | None = None,
    proposed_definition: str | None = None,
    proposed_columns: list[Column] | None = None,
) -> str:
    drop_sql = generate_drop_sql(
        drop_order,
        objects,
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

        definition = proposed_definitions.get(
            oid,
            obj.definition,
        )

        columns = proposed_columns.get(oid)

        if obj.object_type != "TABLE" and not definition:
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
