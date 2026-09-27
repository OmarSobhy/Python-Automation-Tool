from pathlib import Path

from migrator.db import Database
from migrator.executor import execute_migration, verify_migration
from migrator.migration import build_family_migration_plan
from migrator.proposed import parse_proposed_objects


def main():
    sql_files = [
        Path("t_loans_sch_info.sql"),
        Path("v_loans_sch_info.sql"),
        Path("mv_loans_sch_info.sql"),
    ]

    sql_texts = [
        path.read_text(encoding="utf-8")
        for path in sql_files
    ]

    proposed_objects = parse_proposed_objects(
        sql_texts
    )

    db = Database()

    try:
        print("BUILDING PLAN...")
        print()

        plan = build_family_migration_plan(
            db,
            "loans_sch_info",
            proposed_objects,
        )

        print("PLAN READY")
        print()
        print("DROP ORDER:")
        for oid in plan.drop_order:
            obj = plan.objects[oid]
            print(
                f"  {obj.schema}.{obj.name} "
                f"[{obj.object_type}]"
            )

        print()
        print("CREATE ORDER:")
        for oid in plan.create_order:
            obj = plan.objects[oid]
            print(
                f"  {obj.schema}.{obj.name} "
                f"[{obj.object_type}]"
            )

        print()
        print("EXECUTING MIGRATION...")
        print()

        execute_migration(
            db,
            plan,
            plan.objects,
        )

        print("MIGRATION EXECUTED")
        print()

        print("VERIFYING MIGRATION...")
        print()

        verify_migration(
            db,
            plan,
            plan.objects,
        )

        print("VERIFICATION PASSED")
        print()

        print("CHECKING ACTUAL OBJECT TYPES...")
        print()

        checks = [
            ("auto_views", "t_loans_sch_info", "TABLE"),
            ("auto_views", "v_loans_sch_info", "VIEW"),
            ("public", "mv_loans_sch_info", "VIEW"),
        ]

        for schema, name, expected_type in checks:
            rows = db.query(
                """
                SELECT c.relkind
                FROM pg_class c
                JOIN pg_namespace n
                    ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relname = %s;
                """,
                (schema, name),
            )

            if not rows:
                raise RuntimeError(
                    f"Object disappeared: {schema}.{name}"
                )

            relkind = rows[0][0]

            actual_type = {
                "r": "TABLE",
                "v": "VIEW",
                "m": "MATERIALIZED VIEW",
            }.get(relkind, relkind)

            print(
                f"  {schema}.{name}: "
                f"{actual_type}"
            )

            if actual_type != expected_type:
                raise RuntimeError(
                    f"Wrong object type for "
                    f"{schema}.{name}: "
                    f"expected {expected_type}, "
                    f"got {actual_type}"
                )

        print()
        print("CHECKING TABLE IS EMPTY...")
        print()

        rows = db.query(
            """
            SELECT count(*)
            FROM auto_views.t_loans_sch_info;
            """
        )

        row_count = rows[0][0]

        print(
            f"  auto_views.t_loans_sch_info rows: "
            f"{row_count}"
        )

        if row_count != 0:
            raise RuntimeError(
                "Table was recreated with data. "
                "Expected WITH NO DATA."
            )

        print()
        print("CHECKING TEST MARKER...")
        print()

        marker_checks = [
            (
                "auto_views",
                "v_loans_sch_info",
            ),
            (
                "auto_views",
                "t_loans_sch_info",
            ),
            (
                "public",
                "mv_loans_sch_info",
            ),
        ]

        for schema, name in marker_checks:
            rows = db.query(
                f"""
                SELECT migration_test_marker
                FROM "{schema}"."{name}"
                LIMIT 1;
                """
            )

            print(
                f"  {schema}.{name}: "
                "migration_test_marker exists"
            )

        print()
        print("=" * 70)
        print("REHEARSAL PASSED")
        print("=" * 70)
        print()
        print(
            "Rolling back the transaction so the local database "
            "returns to its original state."
        )

        db.connection.rollback()

        print()
        print("ROLLBACK COMPLETE")

    except Exception:
        db.connection.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()