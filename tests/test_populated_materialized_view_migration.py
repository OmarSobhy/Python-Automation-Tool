import uuid

from migrator.migration import build_migration_plan
from migrator.sql_generator import generate_family_migration_sql
from migrator.executor import execute_migration


def test_populated_materialized_view_is_repopulated(db):
    schema = f"mview_test_{uuid.uuid4().hex[:8]}"

    try:
        # ------------------------------------------------------------------
        # Create an isolated test schema.
        # ------------------------------------------------------------------
        db.query(
            f'CREATE SCHEMA "{schema}";'
        )

        # ------------------------------------------------------------------
        # Physical table.
        #
        # This represents an Airflow-managed table.
        # The migration tool must NEVER drop, create, or alter it.
        # ------------------------------------------------------------------
        db.query(
            f"""
            CREATE TABLE "{schema}".source_data (
                id integer,
                customer_name text
            );
            """
        )

        db.query(
            f"""
            INSERT INTO "{schema}".source_data (
                id,
                customer_name
            )
            VALUES
                (1, 'Alice'),
                (2, 'Bob');
            """
        )

        # ------------------------------------------------------------------
        # Create a POPULATED materialized view.
        # ------------------------------------------------------------------
        db.query(
            f"""
            CREATE MATERIALIZED VIEW "{schema}".source_mv AS
            SELECT
                id,
                customer_name
            FROM "{schema}".source_data;
            """
        )

        # ------------------------------------------------------------------
        # Create a dependent view.
        # ------------------------------------------------------------------
        db.query(
            f"""
            CREATE VIEW "{schema}".source_view AS
            SELECT
                id,
                customer_name
            FROM "{schema}".source_mv;
            """
        )

        db.connection.commit()

        # ------------------------------------------------------------------
        # Verify the MV is populated BEFORE migration.
        # ------------------------------------------------------------------
        before_populated = db.query(
            """
            SELECT
                ispopulated
            FROM pg_matviews
            WHERE schemaname = %s
              AND matviewname = %s;
            """,
            (schema, "source_mv"),
        )

        assert before_populated == [(True,)]

        before_mv_rows = db.query(
            f"""
            SELECT
                id,
                customer_name
            FROM "{schema}".source_mv
            ORDER BY id;
            """
        )

        assert before_mv_rows == [
            (1, "Alice"),
            (2, "Bob"),
        ]

        # ------------------------------------------------------------------
        # Proposed TABLE change.
        #
        # This adds a new column to the proposed schema.
        # The physical table itself must remain untouched.
        # ------------------------------------------------------------------
        proposed_sql = f"""
            CREATE TABLE "{schema}".source_data AS
            SELECT
                id,
                customer_name,
                'new'::text AS migration_status
            FROM (
                VALUES
                    (100, 'new'::text)
            ) AS proposed_data(
                id,
                customer_name
            );
        """

        # ------------------------------------------------------------------
        # Build the generic migration plan.
        # ------------------------------------------------------------------
        plan = build_migration_plan(
            db,
            f"{schema}.source_data",
            proposed_sql,
        )

        # ------------------------------------------------------------------
        # Generate the SAME SQL family/executor uses for a plan with
        # proposed definitions.
        # ------------------------------------------------------------------
        generated_sql = generate_family_migration_sql(
            plan,
        )

        print("\n" + "=" * 80)
        print("GENERATED POPULATED-MV MIGRATION SQL")
        print("=" * 80)
        print(generated_sql)
        print("=" * 80)

        # ------------------------------------------------------------------
        # Safety invariants:
        #
        # The physical table must never be executable migration SQL.
        # ------------------------------------------------------------------
        sql_upper = generated_sql.upper()

        assert "DROP TABLE" not in sql_upper
        assert "CREATE TABLE" not in sql_upper
        assert "ALTER TABLE" not in sql_upper

        # ------------------------------------------------------------------
        # The MV was populated before migration, so its captured
        # materialized_view_populated state should cause CREATE MATERIALIZED
        # VIEW to omit WITH NO DATA.
        # ------------------------------------------------------------------
        assert "WITH NO DATA" not in sql_upper

        # ------------------------------------------------------------------
        # The dependency chain must include both:
        #
        # source_data
        #      ↓
        # source_mv
        #      ↓
        # source_view
        # ------------------------------------------------------------------
        object_names = {
            obj.name
            for obj in plan.objects.values()
        }

        assert "source_data" in object_names
        assert "source_mv" in object_names
        assert "source_view" in object_names

        # ------------------------------------------------------------------
        # Execute exactly once.
        # ------------------------------------------------------------------
        execute_migration(
            db,
            plan,
            plan.objects,
        )

        db.connection.commit()

        # ------------------------------------------------------------------
        # Verify physical table data was NOT changed.
        # ------------------------------------------------------------------
        table_rows_after = db.query(
            f"""
            SELECT
                id,
                customer_name
            FROM "{schema}".source_data
            ORDER BY id;
            """
        )

        assert table_rows_after == [
            (1, "Alice"),
            (2, "Bob"),
        ]

        # ------------------------------------------------------------------
        # Verify the materialized view still exists AND is populated.
        # ------------------------------------------------------------------
        after_populated = db.query(
            """
            SELECT
                ispopulated
            FROM pg_matviews
            WHERE schemaname = %s
              AND matviewname = %s;
            """,
            (schema, "source_mv"),
        )

        assert after_populated == [(True,)]

        # ------------------------------------------------------------------
        # Verify MV contains its data after recreation.
        # ------------------------------------------------------------------
        after_mv_rows = db.query(
            f"""
            SELECT
                id,
                customer_name
            FROM "{schema}".source_mv
            ORDER BY id;
            """
        )

        assert after_mv_rows == [
            (1, "Alice"),
            (2, "Bob"),
        ]

        # ------------------------------------------------------------------
        # Verify dependent view still works.
        # ------------------------------------------------------------------
        view_rows = db.query(
            f"""
            SELECT
                id,
                customer_name
            FROM "{schema}".source_view
            ORDER BY id;
            """
        )

        assert view_rows == [
            (1, "Alice"),
            (2, "Bob"),
        ]

    finally:
        # Roll back anything still open before cleanup.
        db.connection.rollback()

        db.query(
            f'DROP SCHEMA IF EXISTS "{schema}" CASCADE;'
        )

        db.connection.commit()
