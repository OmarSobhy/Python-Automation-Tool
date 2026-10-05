import pytest
from migrator.catalog import get_managed_family
from migrator.migration import build_cli_migration_plan
from migrator.models import ProposedObject
from migrator.db import Database
from migrator.executor import (
    execute_migration,
    verify_migration,
)
from migrator.migration import build_family_migration_plan
from migrator.models import ProposedObject
from migrator.sql_generator import generate_family_migration_sql


@pytest.fixture
def family_test_schema():
    db = Database()

    db.query(
        """
        DROP SCHEMA IF EXISTS family_migration_test CASCADE;

        CREATE SCHEMA family_migration_test;

        CREATE TABLE family_migration_test.t_orders (
            id integer,
            customer_name text
        );

        INSERT INTO family_migration_test.t_orders
        VALUES
            (1, 'Alice'),
            (2, 'Bob');

        CREATE VIEW family_migration_test.v_orders AS
        SELECT
            id,
            customer_name
        FROM family_migration_test.t_orders;

        CREATE MATERIALIZED VIEW family_migration_test.mv_orders AS
        SELECT
            id,
            customer_name
        FROM family_migration_test.v_orders;

        CREATE VIEW family_migration_test.v_order_report AS
        SELECT
            id,
            customer_name
        FROM family_migration_test.mv_orders;
        """
    )

    db.connection.commit()

    yield db

    db.connection.rollback()

    db.query(
        """
        DROP SCHEMA IF EXISTS family_migration_test CASCADE;
        """
    )

    db.connection.commit()
    db.close()


def test_end_to_end_family_migration(
    family_test_schema,
):
    db = family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="anything",
            name="v_orders",
            sql="""
                SELECT
                    id,
                    customer_name,
                    'new'::text AS migration_status
                FROM family_migration_test.t_orders
            """,
        ),
    ]

    plan = build_family_migration_plan(
        db,
        "orders",
        proposed_objects,
    )

    assert plan.proposed_definitions

    family_names = {
        obj.name
        for oid, obj in plan.objects.items()
        if oid in plan.proposed_definitions
    }

    assert "v_orders" in family_names
    assert "mv_orders" in family_names

    table_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if obj.name == "t_orders"
    )

    view_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if obj.name == "v_orders"
    )

    materialized_view_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if obj.name == "mv_orders"
    )

    assert plan.objects[table_oid].object_type == "TABLE"
    assert plan.objects[view_oid].object_type == "VIEW"
    assert (
        plan.objects[materialized_view_oid].object_type
        == "MATERIALIZED VIEW"
    )

    table_rows_before = db.query(
        """
        SELECT COUNT(*)
        FROM family_migration_test.t_orders;
        """
    )[0][0]

    assert table_rows_before == 2

    generated_sql = generate_family_migration_sql(plan)

    print(
        "\n"
        + "=" * 80
        + "\nGENERATED FAMILY MIGRATION SQL\n"
        + "=" * 80
        + "\n"
    )

    print(generated_sql)

    print(
        "\n"
        + "=" * 80
        + "\nEND GENERATED FAMILY MIGRATION SQL\n"
        + "=" * 80
        + "\n"
    )

    sql_upper = generated_sql.upper()

    assert "DROP TABLE" not in sql_upper
    assert "CREATE TABLE" not in sql_upper
    assert "ALTER TABLE" not in sql_upper

    execute_migration(
        db,
        plan,
        plan.objects,
    )

    db.connection.commit()

    verify_migration(
        db,
        plan,
        plan.objects,
    )

    rows = db.query(
        """
        SELECT
            c.relname,
            c.relkind,
            CASE
                WHEN c.relkind = 'm'
                THEN c.relispopulated
                ELSE NULL
            END
        FROM pg_class c
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        WHERE n.nspname = 'family_migration_test'
          AND c.relname = ANY(%s);
        """,
        (
            [
                "t_orders",
                "v_orders",
                "mv_orders",
            ],
        ),
    )

    actual = {
        row[0]: (row[1], row[2])
        for row in rows
    }

    assert actual["t_orders"][0] == "r"
    assert actual["v_orders"][0] == "v"
    assert actual["mv_orders"][0] == "m"

    assert actual["mv_orders"][1] is True

    table_rows_after = db.query(
        """
        SELECT COUNT(*)
        FROM family_migration_test.t_orders;
        """
    )[0][0]

    assert table_rows_after == table_rows_before

    view_columns = db.query(
        """
        SELECT
            column_name
        FROM information_schema.columns
        WHERE table_schema = 'family_migration_test'
          AND table_name = 'v_orders'
        ORDER BY ordinal_position;
        """
    )

    assert [
        row[0]
        for row in view_columns
    ] == [
        "id",
        "customer_name",
        "migration_status",
    ]

    mv_columns = db.query(
        """
        SELECT
            a.attname AS column_name
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_class AS c
            ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace AS n
            ON n.oid = c.relnamespace
        WHERE n.nspname = 'family_migration_test'
          AND c.relname = 'mv_orders'
          AND c.relkind = 'm'
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum;
        """
    )

    assert [
        row[0]
        for row in mv_columns
    ] == [
        "id",
        "customer_name",
        "migration_status",
    ]

    mv_definition = db.query(
        """
        SELECT definition
        FROM pg_views
        WHERE schemaname = 'family_migration_test'
          AND viewname = 'mv_orders';
        """
    )

    assert mv_definition == []

    mv_definition = db.query(
        """
        SELECT definition
        FROM pg_matviews
        WHERE schemaname = 'family_migration_test'
          AND matviewname = 'mv_orders';
        """
    )[0][0]

    assert "migration_status" in mv_definition
    assert "legacy_marker" not in mv_definition

    downstream_rows = db.query(
        """
        SELECT
            id,
            customer_name
        FROM family_migration_test.v_order_report
        ORDER BY id;
        """
    )

    assert downstream_rows == [
        (1, "Alice"),
        (2, "Bob"),
    ]


def test_explicit_mv_definition_wins_over_v_definition(
    family_test_schema,
):
    db = family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="family_migration_test",
            name="v_orders",
            sql="""
                SELECT
                    id,
                    customer_name,
                    'from_v'::text AS migration_status
                FROM family_migration_test.t_orders
            """,
        ),
        ProposedObject(
            schema="family_migration_test",
            name="mv_orders",
            sql="""
                SELECT
                    id,
                    customer_name,
                    'explicit_mv'::text AS migration_status
                FROM family_migration_test.v_orders
            """,
        ),
    ]

    plan = build_family_migration_plan(
        db,
        "orders",
        proposed_objects,
    )

    mv_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if obj.name == "mv_orders"
    )

    assert (
        "explicit_mv"
        in plan.proposed_definitions[mv_oid]
    )

    assert (
        "from_v"
        not in plan.proposed_definitions[mv_oid]
    )

def test_explicit_mv_definition_wins_after_execution(
    family_test_schema,
):
    db = family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="family_migration_test",
            name="v_orders",
            sql="""
                SELECT
                    id,
                    customer_name,
                    'from_v'::text AS migration_status
                FROM family_migration_test.t_orders
            """,
        ),
        ProposedObject(
            schema="family_migration_test",
            name="mv_orders",
            sql="""
                SELECT
                    id,
                    customer_name,
                    'explicit_mv'::text AS migration_status
                FROM family_migration_test.v_orders
            """,
        ),
    ]

    plan = build_family_migration_plan(
        db,
        "orders",
        proposed_objects,
    )

    execute_migration(
        db,
        plan,
        plan.objects,
    )

    db.connection.commit()

    verify_migration(
        db,
        plan,
        plan.objects,
    )

    rows = db.query(
        """
        SELECT
            migration_status
        FROM family_migration_test.mv_orders
        ORDER BY id;
        """
    )

    assert rows == [
        ("explicit_mv",),
        ("explicit_mv",),
    ]

def test_family_physical_table_is_not_in_create_or_drop_sql(
    family_test_schema,
):
    db = family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="family_migration_test",
            name="v_orders",
            sql="""
                SELECT
                    id,
                    customer_name,
                    'new'::text AS migration_status
                FROM family_migration_test.t_orders
            """,
        ),
    ]

    plan = build_family_migration_plan(
        db,
        "orders",
        proposed_objects,
    )

    generated_sql = generate_family_migration_sql(plan)

    sql_upper = generated_sql.upper()

    assert "DROP TABLE" not in sql_upper
    assert "CREATE TABLE" not in sql_upper
    assert "ALTER TABLE" not in sql_upper




@pytest.fixture
def simple_family_test_schema():
    db = Database()

    db.query(
        """
        DROP SCHEMA IF EXISTS simple_family_migration_test CASCADE;

        CREATE SCHEMA simple_family_migration_test;

        CREATE TABLE simple_family_migration_test.t_orders (
            id integer,
            customer_name text
        );

        INSERT INTO simple_family_migration_test.t_orders
        VALUES
            (1, 'Alice'),
            (2, 'Bob');

        CREATE VIEW simple_family_migration_test.v_orders AS
        SELECT
            id,
            customer_name
        FROM simple_family_migration_test.t_orders;

        CREATE MATERIALIZED VIEW simple_family_migration_test.mv_orders AS
        SELECT
            id,
            customer_name
        FROM simple_family_migration_test.v_orders;
        """
    )

    db.connection.commit()

    yield db

    db.connection.rollback()

    db.query(
        """
        DROP SCHEMA IF EXISTS simple_family_migration_test CASCADE;
        """
    )

    db.connection.commit()
    db.close()

def test_table_proposal_propagates_to_simple_family_views(
    simple_family_test_schema,
):
    db = simple_family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="simple_family_migration_test",
            name="t_orders",
            sql="""
                CREATE TABLE simple_family_migration_test.t_orders (
                    id integer,
                    customer_name text,
                    migration_status text
                );
            """,
        ),
    ]

    plan = build_family_migration_plan(
        db,
        "orders",
        proposed_objects,
    )

    table_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if obj.name == "t_orders"
    )

    view_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if obj.name == "v_orders"
    )

    assert table_oid not in plan.drop_order
    assert table_oid not in plan.create_order

    assert view_oid in plan.proposed_definitions

    assert "migration_status" in (
        plan.proposed_definitions[view_oid]
    )

    generated_sql = generate_family_migration_sql(plan)

    sql_upper = generated_sql.upper()

    assert "DROP TABLE" not in sql_upper
    assert "CREATE TABLE" not in sql_upper
    assert "ALTER TABLE" not in sql_upper

@pytest.fixture
def complex_family_test_schema():
    db = Database()

    db.query(
        """
        DROP SCHEMA IF EXISTS family_migration_complex_test CASCADE;

        CREATE SCHEMA family_migration_complex_test;

        CREATE TABLE family_migration_complex_test.t_orders (
            id integer,
            customer_id integer,
            amount numeric,
            active boolean
        );

        CREATE TABLE family_migration_complex_test.customers (
            id integer,
            customer_name text
        );

        CREATE VIEW family_migration_complex_test.v_orders AS
        SELECT
            o.id,
            c.customer_name,
            o.amount * 1.2 AS amount_with_tax
        FROM family_migration_complex_test.t_orders o
        JOIN family_migration_complex_test.customers c
            ON c.id = o.customer_id
        WHERE o.active = true;
        """
    )

    db.connection.commit()

    yield db

    db.connection.rollback()

    db.query(
        """
        DROP SCHEMA IF EXISTS family_migration_complex_test CASCADE;
        """
    )

    db.connection.commit()
    db.close()


def test_table_proposal_rejects_unsafe_complex_view_propagation(
    complex_family_test_schema,
):
    db = complex_family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="family_migration_complex_test",
            name="t_orders",
            sql="""
                CREATE TABLE family_migration_complex_test.t_orders (
                    id integer,
                    customer_id integer,
                    amount numeric,
                    active boolean,
                    migration_status text
                );
            """,
        ),
    ]

    with pytest.raises(
        ValueError,
        match="not a simple projection",
    ):
        build_family_migration_plan(
            db,
            "orders",
            proposed_objects,
        )


def test_explicit_complex_v_definition_is_allowed(
    complex_family_test_schema,
):
    db = complex_family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="family_migration_complex_test",
            name="v_orders",
            sql="""
                SELECT
                    o.id,
                    c.customer_name,
                    o.amount * 1.2 AS amount_with_tax,
                    o.active
                FROM family_migration_complex_test.t_orders o
                JOIN family_migration_complex_test.customers c
                    ON c.id = o.customer_id
            """,
        ),
    ]

    plan = build_family_migration_plan(
        db,
        "orders",
        proposed_objects,
    )

    view_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if obj.name == "v_orders"
    )

    definition = plan.proposed_definitions[
        view_oid
    ]

    assert "amount * 1.2" in definition
    assert "JOIN" in definition
    assert "customer_name" in definition


def test_family_migration_rolls_back_on_execution_failure(
    family_test_schema,
):
    db = family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="family_migration_test",
            name="v_orders",
            sql="""
                SELECT
                    id,
                    customer_name,
                    'rollback_test'::text AS migration_status
                FROM family_migration_test.t_orders
            """,
        ),
    ]

    plan = build_family_migration_plan(
        db,
        "orders",
        proposed_objects,
    )

    original_view_definition = db.query(
        """
        SELECT definition
        FROM pg_views
        WHERE schemaname = 'family_migration_test'
          AND viewname = 'v_orders';
        """
    )[0][0]

    original_rows = db.query(
        """
        SELECT *
        FROM family_migration_test.t_orders
        ORDER BY id;
        """
    )

    # Force execution to fail after the migration has begun.
    broken_plan = plan

    broken_plan.proposed_definitions[
        next(
            oid
            for oid, obj in broken_plan.objects.items()
            if obj.name == "v_orders"
        )
    ] = """
        SELECT
            definitely_missing_column
        FROM family_migration_test.t_orders
    """

    with pytest.raises(Exception):
        execute_migration(
            db,
            broken_plan,
            broken_plan.objects,
        )

    # The executor must have rolled the transaction back.
    db.connection.rollback()

    current_view_definition = db.query(
        """
        SELECT definition
        FROM pg_views
        WHERE schemaname = 'family_migration_test'
          AND viewname = 'v_orders';
        """
    )[0][0]

    current_rows = db.query(
        """
        SELECT *
        FROM family_migration_test.t_orders
        ORDER BY id;
        """
    )

    assert current_view_definition == original_view_definition
    assert current_rows == original_rows

def test_table_migration_rolls_back_on_dependent_view_failure(
    family_test_schema,
):
    db = family_test_schema

    # Add a temporary column so the test has a real physical-table change.
    db.query(
        """
        ALTER TABLE family_migration_test.t_orders
        ADD COLUMN rollback_column text;
        """
    )

    db.connection.commit()

    original_columns = db.query(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'family_migration_test'
          AND table_name = 't_orders'
        ORDER BY ordinal_position;
        """
    )

    original_rows = db.query(
        """
        SELECT *
        FROM family_migration_test.t_orders
        ORDER BY id;
        """
    )

    proposed_objects = [
        ProposedObject(
            schema="family_migration_test",
            name="t_orders",
            sql="""
                CREATE TABLE family_migration_test.t_orders (
                    id integer,
                    customer_name text
                );
            """,
        ),
    ]

    # The family planner should recognize the proposed schema.
    plan = build_family_migration_plan(
        db,
        "orders",
        proposed_objects,
    )

    # Force a dependent view definition to fail.
    view_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if obj.name == "v_orders"
    )

    plan.proposed_definitions[view_oid] = """
        SELECT
            definitely_missing_column
        FROM family_migration_test.t_orders
    """

    with pytest.raises(Exception):
        execute_migration(
            db,
            plan,
            plan.objects,
        )

    db.connection.rollback()

    current_columns = db.query(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'family_migration_test'
          AND table_name = 't_orders'
        ORDER BY ordinal_position;
        """
    )

    current_rows = db.query(
        """
        SELECT *
        FROM family_migration_test.t_orders
        ORDER BY id;
        """
    )

    assert current_columns == original_columns
    assert current_rows == original_rows


def test_wrong_schema_same_family_member_name_is_not_managed(
    family_test_schema,
):
    db = family_test_schema

    proposed = ProposedObject(
        schema="public",
        name="v_orders",
        sql="SELECT 1",
    )

    with pytest.raises(Exception) as exc_info:
        build_cli_migration_plan(
            db,
            [proposed],
        )

    assert "managed family" not in str(exc_info.value).lower()

def test_registered_source_view_routes_to_managed_family(
    family_test_schema,
):
    db = family_test_schema

    family = get_managed_family(
        db,
        "orders",
        published_schema="public",
    )

    proposed = ProposedObject(
        schema=family.source_view_schema,
        name=family.source_view_name,
        sql="SELECT id, customer_name FROM family_migration_test.t_orders",
    )

    plan = build_cli_migration_plan(
        db,
        [proposed],
    )

    assert plan.is_family is True


def test_registered_live_table_routes_to_managed_family(
    family_test_schema,
):
    db = family_test_schema

    family = get_managed_family(
        db,
        "orders",
        published_schema="public",
    )

    proposed = ProposedObject(
        schema=family.live_table_schema,
        name=family.live_table_name,
        sql="SELECT id, customer_name FROM family_migration_test.t_orders",
    )

    plan = build_cli_migration_plan(
        db,
        [proposed],
    )

    assert plan.is_family is True