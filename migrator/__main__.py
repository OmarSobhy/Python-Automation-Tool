import sys

from .catalog import discover_families
from .db import Database
from .migration import (
    build_family_migration_plan,
    build_migration_plan,
)
from .proposed import parse_proposed_objects
from .snapshot import get_object
from .sql_generator import (
    generate_family_migration_sql,
    generate_migration_sql,
)


def print_usage():
    print(
        """
Usage:

  python -m migrator discover
  python -m migrator discover <family_name>

  python -m migrator analyze <root> <sql_file>

  python -m migrator plan <root> <sql_file>

  python -m migrator apply <root> <sql_file>

  python -m migrator plan-family <family_name> <sql_file> [<sql_file> ...]

  python -m migrator apply-family <family_name> <sql_file> [<sql_file> ...]
"""
    )


def read_sql_files(paths):
    return [
        open(
            path,
            "r",
            encoding="utf-8",
        ).read()
        for path in paths
    ]


def print_dependency_tree(
    db,
    root,
    dependencies,
):
    objects = {
        root: get_object(
            db,
            root,
        )
    }

    for dependency in dependencies:
        if dependency.dependent_oid not in objects:
            objects[
                dependency.dependent_oid
            ] = get_object(
                db,
                dependency.dependent_oid,
            )

    children = {}

    for dependency in dependencies:
        children.setdefault(
            dependency.referenced_oid,
            [],
        ).append(
            dependency.dependent_oid
        )

    root_oid = objects[root].oid

    def print_node(
        oid,
        prefix="",
        is_last=True,
    ):
        if oid != root_oid:
            connector = (
                "└── "
                if is_last
                else "├── "
            )

            obj = objects[oid]

            print(
                prefix
                + connector
                + f"{obj.schema}.{obj.name} "
                + f"[{obj.object_type}]"
            )

            prefix += (
                "    "
                if is_last
                else "│   "
            )

        child_oids = children.get(
            oid,
            [],
        )

        for index, child_oid in enumerate(
            child_oids
        ):
            print_node(
                child_oid,
                prefix,
                index == len(child_oids) - 1,
            )

    root_obj = objects[root_oid]

    print(
        f"{root_obj.schema}.{root_obj.name} "
        f"[{root_obj.object_type}]"
    )

    for index, child_oid in enumerate(
        children.get(
            root_oid,
            [],
        )
    ):
        print_node(
            child_oid,
            "",
            index == len(
                children[root_oid]
            ) - 1,
        )


def discover(
    db,
    family_name=None,
):
    families = discover_families(db)

    if family_name:
        families = [
            family
            for family in families
            if family.name == family_name
        ]

        if not families:
            raise ValueError(
                "Managed object family not found: "
                f"{family_name}"
            )

    for family in families:
        print(
            f"{family.name}: "
            f"{family.status.value}"
        )

        print(
            f"  source view: "
            f"{family.source_view_schema}."
            f"{family.source_view_name}"
        )

        print(
            f"  live table: "
            f"{family.live_table_schema}."
            f"{family.live_table_name}"
        )

        print(
            f"  published view: "
            f"{family.published_view_schema}."
            f"{family.published_view_name}"
        )

        print()


def analyze(
    db,
    root,
    sql_file,
):
    sql = read_sql_files(
        [sql_file]
    )[0]

    plan = build_migration_plan(
        db,
        root,
        sql,
    )

    print(
        f"ROOT: {plan.root_name}"
    )

    print(
        f"BREAKING: "
        f"{plan.comparison.is_breaking}"
    )

    print()

    print("COLUMN CHANGES:")

    for change in (
        plan.comparison.added
        + plan.comparison.removed
        + plan.comparison.changed
    ):
        print(
            f"  {change.change_type}: "
            f"{change.column_name}"
        )

    print()

    print("IMPACTED OBJECTS:")

    for obj in plan.impacted_objects:
        print(
            f"  {obj.object_name} "
            f"[{obj.object_type}]"
        )

    print()

    print("DEPENDENCY TREE:")

    print_dependency_tree(
        db,
        plan.root_oid,
        plan.dependencies,
    )


def plan(
    db,
    root,
    sql_file,
):
    sql = read_sql_files(
        [sql_file]
    )[0]

    migration_plan = build_migration_plan(
        db,
        root,
        sql,
    )

    print(
        generate_migration_sql(
            migration_plan
        )
    )


def plan_family(
    db,
    family_name,
    sql_files,
):
    sql_contents = read_sql_files(
        sql_files
    )

    proposed_objects = parse_proposed_objects(
        sql_contents
    )

    migration_plan = build_family_migration_plan(
        db,
        family_name,
        proposed_objects,
    )

    print(
        f"FAMILY: {family_name}"
    )

    print()

    print(
        "DROP ORDER:"
    )

    for index, oid in enumerate(
        migration_plan.drop_order,
        start=1,
    ):
        obj = migration_plan.objects[oid]

        print(
            f"{index}. "
            f"{obj.schema}.{obj.name} "
            f"[{obj.object_type}]"
        )

    print()

    print(
        "CREATE ORDER:"
    )

    for index, oid in enumerate(
        migration_plan.create_order,
        start=1,
    ):
        obj = migration_plan.objects[oid]

        definition_source = (
            "NEW SQL"
            if oid
            in migration_plan.proposed_definitions
            else "EXISTING SQL"
        )

        print(
            f"{index}. "
            f"{obj.schema}.{obj.name} "
            f"[{obj.object_type}] "
            f"-> {definition_source}"
        )

    print()

    print(
        "MIGRATION SQL:"
    )

    print(
        generate_family_migration_sql(
            migration_plan
        )
    )

    print()

    print(
        "PLAN READY"
    )


def apply(
    db,
    root,
    sql_file,
):
    from .executor import execute_migration

    sql = read_sql_files(
        [sql_file]
    )[0]

    migration_plan = build_migration_plan(
        db,
        root,
        sql,
    )

    if not migration_plan.validation.is_valid:
        print(
            "MIGRATION BLOCKED"
        )

        for issue in (
            migration_plan.validation.issues
        ):
            print(
                f"{issue.object_name}: "
                f"{issue.column_name} "
                f"{issue.issue_type}"
            )

        return

    migration_sql = generate_migration_sql(
        migration_plan
    )

    print(
        migration_sql
    )

    execute_migration(
        db,
        migration_plan,
    )

    print(
        "MIGRATION APPLIED SUCCESSFULLY"
    )


def apply_family(
    db,
    family_name,
    sql_files,
):
    from .executor import (
        execute_family_migration,
        verify_migration,
    )

    sql_contents = read_sql_files(
        sql_files
    )

    proposed_objects = parse_proposed_objects(
        sql_contents
    )

    migration_plan = build_family_migration_plan(
        db,
        family_name,
        proposed_objects,
    )

    migration_sql = generate_family_migration_sql(
        migration_plan
    )

    print(
        migration_sql
    )

    execute_family_migration(
        db,
        migration_plan,
    )

    print(
        "FAMILY MIGRATION APPLIED SUCCESSFULLY"
    )

    verify_migration(
        db,
        migration_plan,
    )

    print(
        "VERIFICATION PASSED"
    )


def main():
    args = sys.argv[1:]

    if not args:
        print_usage()
        return

    command = args[0]

    db = Database()

    try:
        if command == "discover":
            if len(args) == 1:
                discover(db)

            elif len(args) == 2:
                discover(
                    db,
                    family_name=args[1],
                )

            else:
                print_usage()

        elif command == "analyze":
            if len(args) != 3:
                print_usage()
                return

            analyze(
                db,
                args[1],
                args[2],
            )

        elif command == "plan":
            if len(args) != 3:
                print_usage()
                return

            plan(
                db,
                args[1],
                args[2],
            )

        elif command == "apply":
            if len(args) != 3:
                print_usage()
                return

            apply(
                db,
                args[1],
                args[2],
            )

        elif command == "plan-family":
            if len(args) < 3:
                print_usage()
                return

            plan_family(
                db,
                args[1],
                args[2:],
            )

        elif command == "apply-family":
            if len(args) < 3:
                print_usage()
                return

            apply_family(
                db,
                args[1],
                args[2:],
            )

        else:
            print(
                f"Unknown command: {command}"
            )
            print_usage()

    except Exception as exc:
        print(
            f"ERROR: {exc}"
        )
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()