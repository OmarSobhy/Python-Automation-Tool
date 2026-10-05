import os
import sys

from .executor import verify_migration
from .analysis import analyze_migration
from .catalog import (
    discover_families,
    get_managed_family,
)
from .db import Database
from .executor import execute_migration
from .migration import (
    build_cli_migration_plan,
    build_family_migration_plan,
    build_migration_plan,
)
from .planner import build_dependencies
from .proposed import (
    parse_proposed_object,
    parse_proposed_objects,
)
from .snapshot import get_object
from .sql_generator import (
    generate_family_migration_sql,
    generate_migration_sql,
)
from .sql_utils import qualified_name


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


def discover_sql_file(
    db,
    sql_file,
):
    sql = read_sql_files(
        [sql_file]
    )[0]

    migration_plan = build_cli_migration_plan(
        db,
        sql,
    )

    root_oid = migration_plan.root_oid
    root_obj = migration_plan.objects[root_oid]

    print(
        f"TARGET: {root_obj.schema}.{root_obj.name} "
        f"[{root_obj.object_type}]"
    )

    print(
        f"OID: {root_obj.oid}"
    )

    print()

    print("DEPENDENCY TREE:")

    print_dependency_tree(
        db,
        root_oid,
        migration_plan.dependencies,
    )


def print_usage():
    print(
        """
Usage:

  python -m migrator discover <sql_file>

  python -m migrator analyze <sql_file>
  python -m migrator plan <sql_file>

  python -m migrator apply <sql_file>

  python -m migrator plan-family <family_name> <sql_file> [<sql_file> ...]

  python -m migrator apply-family <family_name> <sql_file> [<sql_file> ...]
"""
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
    sql_file,
):
    sql = read_sql_files(
        [sql_file]
    )[0]

    analysis = analyze_migration(
        db,
        sql,
    )

    migration_plan = analysis["plan"]

    print(
        f"ROOT: {analysis['root_name']}"
    )

    print(
        f"BREAKING: "
        f"{analysis['breaking']}"
    )

    print()

    print("COLUMN CHANGES:")

    for change in analysis["column_changes"]:
        print(
            f"  {change.change_type}: "
            f"{change.column_name}"
        )

    print()

    print("IMPACTED OBJECTS:")

    for obj in analysis["impacted_objects"]:
        print(
            f"  {obj.object_name} "
            f"[{obj.object_type}]"
        )

    print()

    print("DEPENDENCY TREE:")

    print_dependency_tree(
        db,
        migration_plan.root_oid,
        migration_plan.dependencies,
    )


def plan(
    db,
    sql_file,
):
    from .sql_generator import generate_table_alter_sql

    sql = read_sql_files(
        [sql_file]
    )[0]

    migration_plan = build_cli_migration_plan(
        db,
        sql,
    )

    root = migration_plan.root_name

    print(
        f"MIGRATION TARGET: {root}"
    )

    if migration_plan.is_family:

        family_name = root.rsplit(".", 1)[-1]

        if family_name.startswith("v_"):

            family_name = family_name[2:]
    
        elif family_name.startswith("t_"):

            family_name = family_name[2:]

        elif family_name.startswith("mv_"):
     
            family_name = family_name[3:]

        print("MIGRATION MODE: MANAGED FAMILY")
        print(f"FAMILY: {family_name}")

    target = migration_plan.objects.get(
        migration_plan.root_oid
    )

    if target is not None:
        print(
            f"OBJECT TYPE: {target.object_type}"
        )

    print()

    proposed_definition = (
        migration_plan.proposed_definitions.get(
            migration_plan.root_oid
        )
    )

    proposed_columns = (
        migration_plan.proposed_columns.get(
            migration_plan.root_oid
        )
    )

    # --------------------------------------------------------------
    # Managed-family plan
    # --------------------------------------------------------------

    if migration_plan.is_family:
        migration_sql = generate_family_migration_sql(
            migration_plan
        )

        print(
            migration_sql
        )
        return

    # --------------------------------------------------------------
    # Generic migration
    #
    # Print each phase explicitly so the displayed plan exactly
    # matches the operations that will be performed.
    # --------------------------------------------------------------

    print("-- DROP OBJECTS")

    drop_sql = generate_migration_sql(
        migration_plan.drop_order,
        [],
        migration_plan.objects,
        root_oid=migration_plan.root_oid,
        proposed_definition=proposed_definition,
        proposed_columns=proposed_columns,
        comparison=migration_plan.comparison,
    )

    # generate_migration_sql() may contain the drop statements.
    # We only want the DROP phase here, so generate it directly.
    from .sql_generator import generate_drop_sql

    drop_sql = generate_drop_sql(
        migration_plan.drop_order,
        migration_plan.objects,
    )

    if drop_sql.strip():
        print(drop_sql)

    print()

    # --------------------------------------------------------------
    # ALTER ROOT
    # --------------------------------------------------------------

    print("-- ALTER ROOT")

    alter_sql = ""

    if target is not None and target.object_type in {
        "TABLE",
        "PARTITIONED TABLE",
    }:
        alter_sql = generate_table_alter_sql(
            target,
            migration_plan.comparison,
        )

    if alter_sql.strip():
        print(alter_sql)
    else:
        print("-- No root table changes")

    print()

    # --------------------------------------------------------------
    # CREATE OBJECTS
    # --------------------------------------------------------------

    print("-- CREATE OBJECTS")

    create_sql = generate_migration_sql(
        [],
        migration_plan.create_order,
        migration_plan.objects,
        root_oid=migration_plan.root_oid,
        proposed_definition=proposed_definition,
        proposed_columns=proposed_columns,
        comparison=None,
    )

    create_sql = create_sql.replace(
        "-- CREATE OBJECTS\n",
        "",
        1,
    )

    if create_sql.strip():
        print(create_sql)
    else:
        print("-- No objects to create")


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
    sql_file,
):
    sql = read_sql_files(
        [sql_file]
    )[0]

    migration_plan = build_cli_migration_plan(
        db,
        sql,
    )

    print(
        f"MIGRATION TARGET: "
        f"{migration_plan.root_name}"
    )

    target = migration_plan.objects.get(
        migration_plan.root_oid
    )

    if target is not None:
        print(
            f"OBJECT TYPE: "
            f"{target.object_type}"
        )

    print()

    if not migration_plan.validation.is_valid:
        print(
            "MIGRATION BLOCKED"
        )

        for issue in migration_plan.validation.issues:
            print(
                f"{issue.object_name}: "
                f"{issue.column_name} "
                f"{issue.issue_type}"
            )

        return

    proposed_definition = (
        migration_plan.proposed_definitions.get(
            migration_plan.root_oid
        )
    )

    proposed_columns = (
        migration_plan.proposed_columns.get(
            migration_plan.root_oid
        )
    )

    # --------------------------------------------------------------
    # Managed-family plan:
    # multiple proposed definitions require family SQL generation.
    #
    # Generic plan:
    # use the generic generator so arbitrary TABLE roots can emit
    # safe ALTER TABLE statements.
    # --------------------------------------------------------------

    if migration_plan.is_family:
        migration_sql = generate_family_migration_sql(
            migration_plan
        )
    else:
        migration_sql = generate_migration_sql(
            migration_plan.drop_order,
            migration_plan.create_order,
            migration_plan.objects,
            root_oid=migration_plan.root_oid,
            proposed_definition=proposed_definition,
            proposed_columns=proposed_columns,
            comparison=migration_plan.comparison,
        )

    print(
        migration_sql
    )

    execute_migration(
    db,
    migration_plan,
    migration_plan.objects,
)



    verify_migration(
        db,
        migration_plan,
    )

    db.connection.commit()

    print(
        "MIGRATION APPLIED SUCCESSFULLY"
    )

    print(
        "MIGRATION VERIFIED"
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
            if len(args) != 2:
                print_usage()
                return

            target = args[1]

            if os.path.isfile(target):
                discover_sql_file(
                    db,
                    target,
                )
            else:
                discover(
                    db,
                    family_name=target,
                )

        elif command == "analyze":
            if len(args) != 2:
                print_usage()
                return

            analyze(
                db,
                args[1],
            )

        elif command == "plan":
            if len(args) != 2:
                print_usage()
                return

            plan(
                db,
                args[1],
            )

        elif command == "apply":
            if len(args) != 2:
                print_usage()
                return

            apply(
                db,
                args[1],
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