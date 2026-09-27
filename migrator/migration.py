import uuid

from .catalog import (
    get_family_objects,
    get_managed_family,
    resolve_proposed_objects,
)
from .comparator import compare_columns
from .db import Database
from .impact import get_transitive_impact
from .models import (
    Column,
    MigrationPlan,
    SchemaComparison,
)
from .planner import (
    build_dependencies,
    get_create_order,
    get_drop_order,
)
from .proposed import (
    COLUMN_SQL,
    get_proposed_columns,
    parse_proposed_object,
)
from .snapshot import get_object
from .sql_utils import (
    qualified_name,
    quote_identifier,
)
from .validation import validate_migration


def build_migration_plan(
    db: Database,
    root: str,
    proposed_sql: str,
) -> MigrationPlan:
    root_oid, dependencies = build_dependencies(
        db,
        root,
    )

    current = get_object(
        db,
        root_oid,
    )

    proposed = parse_proposed_object(
        proposed_sql,
    )

    proposed_columns = get_proposed_columns(
        db,
        proposed_sql,
    )

    comparison = compare_columns(
        current.columns,
        proposed_columns,
    )

    removed_columns = {
        change.column_name
        for change in comparison.removed
    }

    changed_columns = {
        change.column_name
        for change in comparison.changed
    }

    impacted_objects = get_transitive_impact(
        db,
        root,
        removed_columns | changed_columns,
    )

    validation = validate_migration(
        db,
        root,
        removed_columns,
        changed_columns,
        comparison.added
        + comparison.removed
        + comparison.changed,
    )

    drop_order = get_drop_order(
        root_oid,
        dependencies,
    )

    create_order = get_create_order(
        root_oid,
        dependencies,
    )

    return MigrationPlan(
        root_oid=root_oid,
        root_name=root,
        comparison=comparison,
        proposed_definitions={
            root_oid: proposed.sql,
        },
        objects={
            obj_oid: get_object(db, obj_oid)
            for obj_oid in {
                root_oid,
                *[
                    dependency.dependent_oid
                    for dependency in dependencies
                ],
            }
        },
        impacted_objects=impacted_objects,
        drop_order=drop_order,
        create_order=create_order,
        dependencies=dependencies,
        validation=validation,
    )


def build_family_migration_definitions(
    db,
    family_name,
    proposed_objects,
):
    resolved = resolve_proposed_objects(
        db,
        family_name,
        proposed_objects,
    )

    definitions = {}
    proposed_columns = {}

    # Explicit proposals always win.
    for proposed, oid in resolved:
        definitions[oid] = proposed.sql

        if proposed.name == f"t_{family_name}":
            proposed_columns[oid] = get_proposed_columns(
                db,
                proposed.sql,
                object_type="TABLE",
            )

    # If the family has a proposed table definition,
    # propagate its resulting schema to the other family
    # members.
    family = get_managed_family(
        db,
        family_name,
    )

    table_oid = family.live_table_oid

    if table_oid is not None:
        table_proposal = next(
            (
                proposed
                for proposed, oid in resolved
                if oid == table_oid
            ),
            None,
        )

        if table_proposal is not None:
            columns = proposed_columns[table_oid]

            family_member_oids = {
                family.source_view_oid,
                family.live_table_oid,
                family.published_view_oid,
            }

            family_member_oids.discard(None)

            for oid in family_member_oids:
                if oid == table_oid:
                    continue

                obj = get_object(
                    db,
                    oid,
                )

                if not obj.definition:
                    continue

                definitions[oid] = _propagate_family_columns(
                    db,
                    obj,
                    obj.definition,
                    columns,
                )

    return definitions, proposed_columns


def _propagate_family_columns(
    db,
    obj,
    definition,
    family_columns,
):
    """
    Make newly introduced family columns available from a
    family view/materialized view.

    Existing columns are preserved.

    Columns that are present in the proposed family table but
    absent from the existing family object's definition are
    exposed as NULL::<type>.

    This is generic and does not depend on a specific family
    name or column name.
    """
    if obj.object_type == "TABLE":
        return definition

    if not definition:
        return definition

    existing_columns = _get_definition_columns(
        db,
        definition,
    )

    existing_names = {
        column.name
        for column in existing_columns
    }

    missing = [
        column
        for column in family_columns
        if column.name not in existing_names
    ]

    if not missing:
        return definition

    extra_columns = ",\n".join(
        (
            f"    NULL::{column.data_type} "
            f"AS {quote_identifier(column.name)}"
        )
        for column in missing
    )

    return (
        "SELECT\n"
        "    family_source.*,\n"
        f"{extra_columns}\n"
        "FROM (\n"
        f"{definition.rstrip().rstrip(';')}\n"
        ") AS family_source"
    )


def _get_definition_columns(
    db,
    definition,
):
    candidate_name = (
        f"__family_definition_{uuid.uuid4().hex}"
    )

    candidate = qualified_name(
        "pg_temp",
        candidate_name,
    )

    try:
        db.query(
            f"""
            CREATE TEMP VIEW {candidate_name} AS
            {definition}
            """
        )

        rows = db.query(
            COLUMN_SQL,
            (candidate,),
        )

        return [
            Column(
                name=name,
                data_type=data_type,
                nullable=nullable,
                position=position,
            )
            for (
                position,
                name,
                data_type,
                nullable,
            ) in rows
        ]

    finally:
        try:
            db.query(
                f"""
                DROP VIEW IF EXISTS {candidate_name};
                """
            )
        except Exception:
            db.connection.rollback()


def get_family_migration_objects(
    db: Database,
    family_name: str,
    published_schema: str | None = None,
):
    family = get_managed_family(
        db,
        family_name,
        published_schema=published_schema,
    )

    objects = {}

    for oid, _, _, _ in get_family_objects(family):
        objects[oid] = get_object(db, oid)

    return objects


def build_family_dependencies(
    db,
    objects,
):
    all_dependencies = {}

    for oid, obj in objects.items():
        _, dependencies = build_dependencies(
            db,
            f"{obj.schema}.{obj.name}",
        )

        for dependency in dependencies:
            # PostgreSQL can expose self-references through
            # pg_rewrite/pg_depend. They are not real migration
            # dependencies.
            if dependency.referenced_oid == dependency.dependent_oid:
                continue

            # Keep only dependency edges between objects that are
            # actually part of this migration plan.
            if (
                dependency.referenced_oid not in objects
                or dependency.dependent_oid not in objects
            ):
                continue

            key = (
                dependency.referenced_oid,
                dependency.dependent_oid,
            )

            all_dependencies[key] = dependency

    return list(all_dependencies.values())


def build_family_orders(
    objects,
    dependencies,
):
    object_ids = set(objects)

    dependency_map = {
        oid: []
        for oid in object_ids
    }

    for dependency in dependencies:
        if (
            dependency.referenced_oid in object_ids
            and dependency.dependent_oid in object_ids
        ):
            dependency_map[
                dependency.dependent_oid
            ].append(
                dependency.referenced_oid
            )

    drop_order = []
    drop_seen = set()

    def visit_for_drop(oid):
        if oid in drop_seen:
            return

        drop_seen.add(oid)

        for referenced_oid in dependency_map.get(
            oid,
            [],
        ):
            visit_for_drop(referenced_oid)

        drop_order.append(oid)

    for oid in object_ids:
        visit_for_drop(oid)

    create_order = []
    create_seen = set()

    def visit_for_create(oid):
        if oid in create_seen:
            return

        for referenced_oid in dependency_map.get(
            oid,
            [],
        ):
            visit_for_create(referenced_oid)

        create_seen.add(oid)
        create_order.append(oid)

    for oid in object_ids:
        visit_for_create(oid)

    drop_order.reverse()

    return drop_order, create_order


def build_family_migration_plan(
    db,
    family_name,
    proposed_objects,
):
    published_schema = None

    for proposed in proposed_objects:
        if (
            proposed.name == f"mv_{family_name}"
            and proposed.schema
        ):
            published_schema = proposed.schema
            break

    definitions, proposed_columns = (
        build_family_migration_definitions(
            db,
            family_name,
            proposed_objects,
        )
    )

    family = get_managed_family(
        db,
        family_name,
        published_schema=published_schema,
    )

    family_objects = get_family_migration_objects(
        db,
        family_name,
        published_schema=published_schema,
    )

    dependency_objects = get_family_dependency_objects(
        db,
        family_objects,
    )

    objects = {
        **family_objects,
        **dependency_objects,
    }

    dependencies = build_family_dependencies(
        db,
        objects,
    )

    drop_order, create_order = build_family_orders(
        objects,
        dependencies,
    )

    root_oid = family.source_view_oid

    if root_oid is None:
        root_oid = family.live_table_oid

    if root_oid is None:
        root_oid = family.published_view_oid

    if root_oid is None:
        raise ValueError(
            f"Managed object family has no migration root: "
            f"{family_name}"
        )

    return MigrationPlan(
        root_oid=root_oid,
        root_name=family_name,
        comparison=SchemaComparison(),
        proposed_definitions=definitions,
        proposed_columns=proposed_columns,
        objects=objects,
        dependencies=dependencies,
        drop_order=drop_order,
        create_order=create_order,
    )


def get_family_dependency_objects(
    db,
    objects,
):
    dependency_objects = {}
    known_objects = dict(objects)

    pending = list(objects.values())

    while pending:
        current = pending.pop()

        _, dependencies = build_dependencies(
            db,
            f"{current.schema}.{current.name}",
        )

        for dependency in dependencies:
            # Dependency direction is:
            #
            # referenced_oid -> dependent_oid
            #
            # We only want objects that depend on the
            # current family/dependency object. Do not
            # walk backwards into upstream objects.
            if dependency.referenced_oid != current.oid:
                continue

            dependent_oid = dependency.dependent_oid

            if dependent_oid in known_objects:
                continue

            try:
                obj = get_object(
                    db,
                    dependent_oid,
                )
            except ValueError:
                continue

            known_objects[dependent_oid] = obj
            dependency_objects[dependent_oid] = obj
            pending.append(obj)

    return dependency_objects