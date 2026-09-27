from migrator.models import (
    Column,
    DatabaseObject,
    MigrationPlan,
    SchemaComparison,
)
from migrator.sql_generator import (
    generate_create_sql,
    generate_family_create_sql,
    generate_family_migration_sql,
    generate_migration_sql,
    generate_object_create_sql,
)


def test_generate_create_sql_does_not_change_owner():
    obj = DatabaseObject(
        oid=1,
        schema="demo",
        name="v_customer",
        object_type="VIEW",
        definition="SELECT 1",
        owner="postgres",
    )

    sql = generate_create_sql(
        create_order=[1],
        objects={1: obj},
    )

    assert (
        'ALTER VIEW "demo"."v_customer" '
        'OWNER TO "postgres";'
        not in sql
    )


def test_generate_create_sql_preserves_unpopulated_materialized_view():
    obj = DatabaseObject(
        oid=1,
        schema="demo",
        name="mv_customer_stats",
        object_type="MATERIALIZED VIEW",
        definition="SELECT 1",
        owner="postgres",
        materialized_view_populated=False,
    )

    sql = generate_create_sql(
        create_order=[1],
        objects={1: obj},
    )

    assert (
        'CREATE MATERIALIZED VIEW "demo"."mv_customer_stats" AS\n'
        "SELECT 1 WITH NO DATA;"
        in sql
    )

    assert (
        'ALTER MATERIALIZED VIEW "demo"."mv_customer_stats" '
        'OWNER TO "postgres";'
        not in sql
    )


def test_generated_migration_sql_drops_and_creates_in_reverse_order():
    objects = {
        100: DatabaseObject(
            oid=100,
            schema="test_schema",
            name="v_root",
            object_type="VIEW",
            definition="SELECT 1",
        ),
        200: DatabaseObject(
            oid=200,
            schema="test_schema",
            name="v_level_1",
            object_type="VIEW",
            definition="SELECT 1 FROM test_schema.v_root",
        ),
        300: DatabaseObject(
            oid=300,
            schema="test_schema",
            name="mv_level_2",
            object_type="MATERIALIZED VIEW",
            definition="SELECT 1 FROM test_schema.v_level_1",
        ),
    }

    drop_order = [
        300,
        200,
        100,
    ]

    create_order = [
        100,
        200,
        300,
    ]

    sql = generate_migration_sql(
        drop_order,
        create_order,
        objects,
        root_oid=100,
        proposed_definition="SELECT 1;",
    )

    drop_mv = sql.index(
        'DROP MATERIALIZED VIEW "test_schema"."mv_level_2";'
    )

    drop_view = sql.index(
        'DROP VIEW "test_schema"."v_level_1";'
    )

    drop_root = sql.index(
        'DROP VIEW "test_schema"."v_root";'
    )

    create_root = sql.index(
        'CREATE VIEW "test_schema"."v_root"'
    )

    create_view = sql.index(
        'CREATE VIEW "test_schema"."v_level_1"'
    )

    create_mv = sql.index(
        'CREATE MATERIALIZED VIEW "test_schema"."mv_level_2"'
    )

    assert drop_mv < drop_view < drop_root
    assert create_root < create_view < create_mv


def test_generate_object_create_sql_for_table():
    obj = DatabaseObject(
        oid=123,
        schema="auto_views",
        name="t_loans_info",
        object_type="TABLE",
        columns=[
            Column(
                name="id",
                data_type="integer",
                nullable=True,
                position=1,
            ),
        ],
    )

    sql = generate_object_create_sql(
        obj,
        None,
    )

    assert (
        'CREATE TABLE "auto_views"."t_loans_info" AS'
        in sql
    )

    assert (
        'NULL::integer AS "id"'
        in sql
    )

    assert "WITH NO DATA;" in sql


def test_generate_family_create_sql_uses_proposed_definition_for_changed_object():
    obj = DatabaseObject(
        oid=123,
        schema="auto_views",
        name="v_any_name",
        object_type="VIEW",
        definition="SELECT 1",
    )

    sql = generate_family_create_sql(
        create_order=[123],
        objects={123: obj},
        proposed_definitions={
            123: "SELECT 2",
        },
    )

    assert "CREATE VIEW" in sql
    assert "SELECT 2" in sql
    assert "SELECT 1" not in sql


def test_generate_family_create_sql_uses_existing_definition_for_unchanged_object():
    obj = DatabaseObject(
        oid=456,
        schema="auto_views",
        name="v_downstream",
        object_type="VIEW",
        definition="SELECT 1 AS id",
    )

    sql = generate_family_create_sql(
        create_order=[456],
        objects={456: obj},
        proposed_definitions={},
    )

    assert "CREATE VIEW" in sql
    assert "SELECT 1 AS id" in sql


def test_generate_family_create_sql_fails_for_table_without_columns():
    obj = DatabaseObject(
        oid=789,
        schema="auto_views",
        name="t_downstream",
        object_type="TABLE",
        definition=None,
    )

    try:
        generate_family_create_sql(
            create_order=[789],
            objects={789: obj},
            proposed_definitions={},
        )
    except ValueError as exc:
        assert (
            str(exc)
            == 'Table has no captured columns: '
            '"auto_views"."t_downstream"'
        )
    else:
        raise AssertionError(
            "Expected ValueError for table without columns"
        )


def test_generate_family_migration_sql():
    changed = DatabaseObject(
        oid=1,
        schema="auto_views",
        name="v_any_name",
        object_type="VIEW",
        definition="SELECT 1",
    )

    unchanged = DatabaseObject(
        oid=2,
        schema="auto_views",
        name="v_downstream",
        object_type="VIEW",
        definition="SELECT 2",
    )

    plan = MigrationPlan(
        root_oid=1,
        root_name="any_name",
        comparison=SchemaComparison(),
        proposed_definitions={
            1: "SELECT 10",
        },
        objects={
            1: changed,
            2: unchanged,
        },
        drop_order=[2, 1],
        create_order=[1, 2],
    )

    sql = generate_family_migration_sql(plan)

    assert (
        'DROP VIEW "auto_views"."v_downstream";'
        in sql
    )

    assert (
        'DROP VIEW "auto_views"."v_any_name";'
        in sql
    )

    assert (
        'CREATE VIEW "auto_views"."v_any_name" AS'
        in sql
    )

    assert "SELECT 10" in sql

    assert (
        'CREATE VIEW "auto_views"."v_downstream" AS'
        in sql
    )

    assert "SELECT 2" in sql


def test_generate_family_migration_sql_has_sections():
    changed = DatabaseObject(
        oid=1,
        schema="auto_views",
        name="v_any_name",
        object_type="VIEW",
        definition="SELECT 1",
    )

    plan = MigrationPlan(
        root_oid=1,
        root_name="any_name",
        comparison=SchemaComparison(),
        proposed_definitions={
            1: "SELECT 10",
        },
        objects={
            1: changed,
        },
        drop_order=[1],
        create_order=[1],
    )

    sql = generate_family_migration_sql(plan)

    assert sql.startswith("-- DROP OBJECTS")
    assert "\n\n-- CREATE OBJECTS\n" in sql
    assert "SELECT 10" in sql