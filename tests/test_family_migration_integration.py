import pytest

from migrator.db import Database
from migrator.executor import (
    execute_migration,
    verify_migration,
)
from migrator.migration import build_family_migration_plan
from migrator.models import ProposedObject


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

    yield db

    db.query(
        """
        DROP SCHEMA IF EXISTS family_migration_test CASCADE;
        """
    )

    db.close()


def test_end_to_end_family_migration(
    family_test_schema,
):
    db = family_test_schema

    proposed_objects = [
        ProposedObject(
            schema="anything",
            name="t_orders",
            sql="""
                SELECT
                    100 AS id,
                    'new'::text AS customer_name
            """,
        ),
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
        ProposedObject(
            schema="anything",
            name="mv_orders",
            sql="""
                SELECT
                    id,
                    customer_name,
                    'new'::text AS migration_status
                FROM family_migration_test.v_orders
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

    assert family_names == {
        "t_orders",
        "v_orders",
        "mv_orders",
    }

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

    table_rows = db.query(
        """
        SELECT COUNT(*)
        FROM family_migration_test.t_orders;
        """
    )

    assert table_rows[0][0] == 0

    downstream_rows = db.query(
        """
        SELECT
            id,
            customer_name
        FROM family_migration_test.v_order_report
        ORDER BY id;
        """
    )

    assert downstream_rows == []

def test_debug_family_objects():
    db = Database()

    try:
        db.query(
            """
            DROP SCHEMA IF EXISTS family_migration_test CASCADE;

            CREATE SCHEMA family_migration_test;

            CREATE TABLE family_migration_test.t_orders (
                id integer,
                customer_name text
            );

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
            """
        )

        rows = db.query(
            """
            SELECT
                n.nspname,
                c.relname,
                c.relkind
            FROM pg_class c
            JOIN pg_namespace n
                ON n.oid = c.relnamespace
            WHERE n.nspname = 'family_migration_test'
            ORDER BY c.relname;
            """
        )

        print("\nFAMILY DEBUG:")
        for row in rows:
            print(row)

        assert any(
            row[1] == "mv_orders"
            and row[2] == "m"
            for row in rows
        )

    finally:
        db.query(
            """
            DROP SCHEMA IF EXISTS family_migration_test CASCADE;
            """
        )
        db.close()