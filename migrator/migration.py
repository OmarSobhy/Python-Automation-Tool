from __future__ import annotations

import re

from .catalog import get_managed_family
from .change_safety import is_safe_change
from .comparator import compare_columns
from .db import Database
from .dependencies import get_dependency_edges, get_dependency_tree
from .impact import get_transitive_impact
from .models import (
    Column,
    DatabaseObject,
    Dependency,
    ImpactedObject,
    MigrationPlan,
    MigrationValidation,
    ProposedObject,
    ValidationIssue,
)
from .snapshot import get_object
from .proposed import get_proposed_columns, parse_proposed_object


def _is_physical_table(obj: DatabaseObject) -> bool:
    return obj.object_type in {
        "TABLE",
        "PARTITIONED TABLE",
    }


def _get_root_and_objects(
    db: Database,
    root: str,
) -> tuple[DatabaseObject, dict[int, DatabaseObject]]:
    row = db.query(
        """
        SELECT c.oid
        FROM pg_class c
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        WHERE n.nspname || '.' || c.relname = %s
        """,
        (root,),
    )

    if not row:
        raise ValueError(
            f"Object {root} was not found"
        )

    root_oid = row[0][0]
    root_object = get_object(db, root_oid)

    rows = get_dependency_tree(db, root)

    objects: dict[int, DatabaseObject] = {
        root_object.oid: root_object,
    }

    for oid, _depth, _object_name, _relkind in rows:
        if oid not in objects:
            objects[oid] = get_object(db, oid)

    return root_object, objects


def _get_changed_columns(
    comparison,
) -> set[str]:
    changed = set()

    for change in comparison.added:
        changed.add(change.column_name)

    for change in comparison.removed:
        changed.add(change.column_name)

    for change in comparison.changed:
        changed.add(change.column_name)

    return changed


def _build_dependencies(
    db: Database,
    objects: dict[int, DatabaseObject],
) -> list[Dependency]:
    if not objects:
        return []

    return get_dependency_edges(db, set(objects.keys()))


def _dependency_order(
    root_oid: int,
    objects: dict[int, DatabaseObject],
    dependencies: list[Dependency],
    reverse: bool = False,
) -> list[int]:
    object_oids = set(objects.keys())

    children: dict[int, list[int]] = {
        oid: [] for oid in object_oids
    }

    for dependency in dependencies:
        if (
            dependency.referenced_oid in object_oids
            and dependency.dependent_oid in object_oids
        ):
            children[dependency.referenced_oid].append(
                dependency.dependent_oid
            )

    for oid in children:
        children[oid].sort()

    result: list[int] = []
    visited: set[int] = set()

    def visit(oid: int) -> None:
        if oid in visited:
            return

        visited.add(oid)

        for child in children.get(oid, []):
            visit(child)

        result.append(oid)

    visit(root_oid)

    return list(reversed(result)) if reverse else result


def _dependency_order_for_oids(
    object_oids: set[int],
    dependencies: list[Dependency],
    reverse: bool = False,
) -> list[int]:
    """
    Order an arbitrary/disconnected set of migration objects.

    A family migration can contain objects which are logically related
    but are not connected by PostgreSQL dependency edges, e.g.

        auto_views.v_collection_base
        public.mv_collection_base

    Therefore ordering from one root is insufficient. We order the
    complete migration object set instead.
    """
    if not object_oids:
        return []

    children: dict[int, list[int]] = {
        oid: [] for oid in object_oids
    }

    for dependency in dependencies:
        if (
            dependency.referenced_oid in object_oids
            and dependency.dependent_oid in object_oids
            and dependency.referenced_oid != dependency.dependent_oid
        ):
            children[dependency.referenced_oid].append(
                dependency.dependent_oid
            )

    for oid in children:
        children[oid].sort()

    result: list[int] = []
    visited: set[int] = set()

    def visit(oid: int) -> None:
        if oid in visited:
            return

        visited.add(oid)

        for child in children.get(oid, []):
            visit(child)

        result.append(oid)

    for oid in sorted(object_oids):
        visit(oid)

    if reverse:
        result.reverse()

    return result


def get_drop_order(
    root_oid: int,
    dependencies: list[Dependency],
    objects: dict[int, DatabaseObject],
) -> list[int]:
    # Dependents must be dropped before the objects they reference.
    return _dependency_order(
        root_oid,
        objects,
        dependencies,
        reverse=False,
    )


def get_create_order(
    root_oid: int,
    dependencies: list[Dependency],
    objects: dict[int, DatabaseObject],
) -> list[int]:
    # Referenced objects must be created before their dependents.
    return _dependency_order(
        root_oid,
        objects,
        dependencies,
        reverse=True,
    )


def _resolve_family_proposals(
    db: Database,
    family_name: str,
    proposed_objects: list[ProposedObject],
) -> tuple[object, list[tuple[ProposedObject, int]]]:
    family = get_managed_family(db, family_name)

    # Managed-family identity is determined by the family member name.
    #
    # The schema supplied in the migration SQL is not used to resolve
    # the actual PostgreSQL object. This allows callers to provide SQL
    # such as:
    #
    #     CREATE/SELECT ... v_orders ...
    #
    # while the managed-family catalog determines the actual schema.
    object_map = {
        family.source_view_name: family.source_view_oid,
        family.live_table_name: family.live_table_oid,
        family.published_view_name: family.published_view_oid,
    }

    resolved: list[tuple[ProposedObject, int]] = []

    for proposed in proposed_objects:
        oid = object_map.get(proposed.name)

        if oid is None:
            raise ValueError(
                f"Object {proposed.schema}.{proposed.name} "
                f"is not a member of managed family {family_name}"
            )

        resolved.append((proposed, oid))

    return family, resolved


def _family_object_map(
    family,
) -> dict[str, int]:
    result: dict[str, int] = {}

    if (
        family.source_view_schema
        and family.source_view_name
        and family.source_view_oid is not None
    ):
        result[
            f"{family.source_view_schema}.{family.source_view_name}"
        ] = family.source_view_oid

    if (
        family.live_table_schema
        and family.live_table_name
        and family.live_table_oid is not None
    ):
        result[
            f"{family.live_table_schema}.{family.live_table_name}"
        ] = family.live_table_oid

    if (
        family.published_view_schema
        and family.published_view_name
        and family.published_view_oid is not None
    ):
        result[
            f"{family.published_view_schema}.{family.published_view_name}"
        ] = family.published_view_oid

    return result


def _parse_simple_projection_view(
    definition: str,
) -> tuple[list[str], str] | None:
    """
    Accept only a simple projection:

        SELECT
            column_a,
            column_b
        FROM schema.table

    Complex SQL is rejected because automatically rewriting it could
    silently destroy business logic.
    """

    text = definition.strip().rstrip(";").strip()

    match = re.match(
        r"""
        ^\s*
        SELECT\s+
        (?P<select>.*?)
        \s+
        FROM\s+
        (?P<from>
            (?:
                "(?:[^"]|"")+"?"
                |
                [A-Za-z_][A-Za-z0-9_]*
            )
            \s*\.\s*
            (?:
                "(?:[^"]|"")+"?"
                |
                [A-Za-z_][A-Za-z0-9_]*
            )
        )
        \s*$
        """,
        text,
        re.IGNORECASE | re.DOTALL | re.VERBOSE,
    )

    if match is None:
        return None

    select_part = match.group("select").strip()
    from_part = match.group("from").strip()

    forbidden = (
        "DISTINCT",
        "JOIN",
        "WHERE",
        "GROUP",
        "HAVING",
        "ORDER",
        "LIMIT",
        "OFFSET",
        "UNION",
        "INTERSECT",
        "EXCEPT",
        "CASE",
        "OVER",
    )

    upper = text.upper()

    for keyword in forbidden:
        if re.search(
            rf"\b{re.escape(keyword)}\b",
            upper,
        ):
            return None

    if "(" in select_part or ")" in select_part:
        return None

    raw_columns = [
        item.strip()
        for item in select_part.split(",")
    ]

    if not raw_columns:
        return None

    columns: list[str] = []

    for column in raw_columns:
        if re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            column,
        ):
            columns.append(column)
            continue

        if re.fullmatch(
            r'"[^"]+"',
            column,
        ):
            columns.append(column[1:-1])
            continue

        return None

    return columns, from_part


def _replace_select_columns(
    definition: str,
    columns: list[Column],
) -> str:
    """
    Replace the SELECT list only when the existing view is a simple
    projection.

    Complex views are rejected rather than silently rewritten.
    """

    parsed = _parse_simple_projection_view(
        definition
    )

    if parsed is None:
        raise ValueError(
            "Automatic family column propagation is unsafe for "
            "this view. The view is not a simple projection. "
            "Provide explicit v_<family>.sql / mv_<family>.sql "
            "instead."
        )

    _existing_columns, from_clause = parsed

    if not columns:
        raise ValueError(
            "Cannot automatically propagate an empty table schema."
        )

    column_lines = [
        f'"{column.name}"'
        for column in columns
    ]

    replacement = ",\n    ".join(
        column_lines
    )

    return (
        "SELECT\n"
        f"    {replacement}\n"
        f"FROM {from_clause}"
    )


def _propagate_family_columns(
    db: Database,
    family,
    definitions: dict[int, str],
    proposed_columns: dict[int, list[Column]],
    explicit_oids: set[int],
) -> None:
    """
    Propagate a proposed t_<family> schema to family views unless
    those views were explicitly supplied by the user.

    Explicit SQL always wins.
    """
    table_oid = family.live_table_oid

    if table_oid is None:
        return

    table_columns = proposed_columns.get(table_oid)

    if not table_columns:
        return

    source_oid = family.source_view_oid
    published_oid = family.published_view_oid

    if (
        source_oid is not None
        and source_oid not in explicit_oids
        and source_oid in definitions
    ):
        definitions[source_oid] = _replace_select_columns(
            definitions[source_oid],
            table_columns,
        )

    if (
        published_oid is not None
        and published_oid not in explicit_oids
        and published_oid in definitions
    ):
        definitions[published_oid] = _replace_select_columns(
            definitions[published_oid],
            table_columns,
        )

    if source_oid is not None and source_oid not in explicit_oids:
        source_object = get_object(db, source_oid)

        if source_object.definition:
            definitions[source_oid] = _replace_select_columns(
                source_object.definition,
                table_columns,
            )

    if published_oid is not None and published_oid not in explicit_oids:
        published_object = get_object(db, published_oid)

        if published_object.definition:
            definitions[published_oid] = _replace_select_columns(
                published_object.definition,
                table_columns,
            )


def _copy_definition_for_published_view(
    source_sql: str,
    published_schema: str,
    published_name: str,
) -> str:
    """
    Build the published family view from the canonical v_<family>
    SELECT definition.

    The returned value must be only the SELECT/query body because
    generate_object_create_sql() adds the CREATE VIEW clause.
    """
    body = source_sql.strip().rstrip(";").strip()

    create_match = re.match(
        r"""
        ^CREATE\s+
        (?:OR\s+REPLACE\s+)?
        (?:MATERIALIZED\s+VIEW|VIEW)
        \s+
        (?:
            "(?:[^"]|"")+"?"
            |
            [A-Za-z_][A-Za-z0-9_]*
        )
        (?:\s*\.\s*
            (?:
                "(?:[^"]|"")+"?"
                |
                [A-Za-z_][A-Za-z0-9_]*
            )
        )?
        \s+AS\s+
        """,
        body,
        re.IGNORECASE | re.VERBOSE,
    )

    if create_match:
        body = body[create_match.end():].strip()

    if not body:
        raise ValueError(
            f"Unable to build published view "
            f"{published_schema}.{published_name}: empty source definition"
        )

    return body


def build_family_migration_definitions(
    db: Database,
    family_name: str,
    proposed_objects: list[ProposedObject],
):
    family, resolved = _resolve_family_proposals(
        db,
        family_name,
        proposed_objects,
    )

    family_objects: dict[int, DatabaseObject] = {}

    for oid in _family_object_map(family).values():
        family_objects[oid] = get_object(db, oid)

    definitions: dict[int, str] = {}
    proposed_columns: dict[int, list[Column]] = {}
    explicit_oids: set[int] = set()

    for proposed, oid in resolved:
        explicit_oids.add(oid)

        definitions[oid] = proposed.sql

        object_type = family_objects[oid].object_type

        proposed_columns[oid] = get_proposed_columns(
            db,
            proposed.sql,
            object_type=object_type,
        )

    source_oid = family.source_view_oid
    published_oid = family.published_view_oid
    table_oid = family.live_table_oid

    # v_<family> is canonical for managed-family migrations.
    #
    # If v_ is explicitly supplied and mv_ is not, create mv_ from the
    # exact same SELECT body.
    if (
        source_oid is not None
        and source_oid in explicit_oids
        and published_oid is not None
        and published_oid not in explicit_oids
    ):
        source_sql = definitions[source_oid]

        if not family.published_view_schema or not family.published_view_name:
            raise ValueError(
                f"Published view information is incomplete for "
                f"family {family_name}"
            )

        definitions[published_oid] = _copy_definition_for_published_view(
            source_sql,
            family.published_view_schema,
            family.published_view_name,
        )

        proposed_columns[published_oid] = get_proposed_columns(
            db,
            definitions[published_oid],
            object_type="VIEW",
        )

    # If t_<family> is explicitly supplied, propagate its proposed
    # columns into family views that were not explicitly supplied.
    if table_oid is not None and table_oid in explicit_oids:
        _propagate_family_columns(
            db,
            family,
            definitions,
            proposed_columns,
            explicit_oids,
        )

    return (
        family,
        resolved,
        definitions,
        proposed_columns,
    )


def build_family_dependencies(
    db: Database,
    root_oid: int,
    migration_oids: set[int],
    family,
) -> list[Dependency]:
    if not migration_oids:
        return []

    dependencies = get_dependency_edges(
        db,
        migration_oids,
    )

    # Managed-family relationship:
    # the published mv_<family> is generated from the canonical
    # v_<family> definition when v_ is supplied and mv_ is not
    # explicitly supplied.
    #
    # PostgreSQL does not expose this as a dependency because the
    # generated mv_ SQL contains the same SELECT body rather than
    # referencing v_. The migration planner nevertheless needs this
    # logical edge so DROP/CREATE ordering is deterministic.
    source_oid = family.source_view_oid
    published_oid = family.published_view_oid

    if (
        source_oid is not None
        and published_oid is not None
        and source_oid in migration_oids
        and published_oid in migration_oids
    ):
        family_dependency = Dependency(
            referenced_oid=source_oid,
            dependent_oid=published_oid,
        )

        if family_dependency not in dependencies:
            dependencies.append(family_dependency)

    return dependencies


def build_migration_plan(
    db: Database,
    root: str,
    proposed_definition: str | None = None,
) -> MigrationPlan:
    root_object, objects = _get_root_and_objects(
        db,
        root,
    )

    root_oid = root_object.oid

    comparison = compare_columns(
        root_object.columns,
        (
            get_proposed_columns(
                db,
                proposed_definition,
                object_type=root_object.object_type,
            )
            if proposed_definition
            else root_object.columns
        ),
    )

    changed_columns = _get_changed_columns(comparison)

    impacted_objects = get_transitive_impact(
        db,
        root,
        changed_columns,
    )

    for impacted in impacted_objects:
        if impacted.oid not in objects:
            objects[impacted.oid] = get_object(
                db,
                impacted.oid,
            )

    dependencies = _build_dependencies(
        db,
        objects,
    )

    drop_order = get_drop_order(
        root_oid,
        dependencies,
        objects,
    )

    create_order = get_create_order(
        root_oid,
        dependencies,
        objects,
    )

    validation = MigrationValidation()

    for change in (
        comparison.added
        + comparison.removed
        + comparison.changed
    ):
        if change.change_type == "REMOVED":
            continue

        if not is_safe_change(change):
            validation.issues.append(
                ValidationIssue(
                    object_name=(
                        root_object.schema
                        + "."
                        + root_object.name
                    ),
                    object_type=root_object.object_type,
                    column_name=change.column_name,
                    issue_type=(
                        change.change_reason
                        or change.change_type
                    ),
                )
            )

    return MigrationPlan(
        root_oid=root_oid,
        root_name=(
            f"{root_object.schema}.{root_object.name}"
        ),
        comparison=comparison,
        is_family=False,
        proposed_definitions=(
            {root_oid: proposed_definition}
            if proposed_definition
            else {}
        ),
        proposed_columns={},
        objects=objects,
        impacted_objects=impacted_objects,
        drop_order=drop_order,
        create_order=create_order,
        dependencies=dependencies,
        validation=validation,
    )


def build_family_migration_plan(
    db: Database,
    family_name: str,
    proposed_objects: list[ProposedObject],
) -> MigrationPlan:
    (
        family,
        resolved,
        definitions,
        proposed_columns,
    ) = build_family_migration_definitions(
        db,
        family_name,
        proposed_objects,
    )

    family_object_oids = set(
        _family_object_map(family).values()
    )

    objects: dict[int, DatabaseObject] = {}

    for oid in family_object_oids:
        objects[oid] = get_object(
            db,
            oid,
        )

    explicit_oids = {
        oid
        for _proposed, oid in resolved
    }

    root_oid = resolved[0][1]
    root_object = objects[root_oid]

    proposed_root_columns = proposed_columns.get(
        root_oid,
        root_object.columns,
    )

    comparison = compare_columns(
        root_object.columns,
        proposed_root_columns,
    )

    changed_columns = _get_changed_columns(
        comparison,
    )

    seed_oids = set(
        definitions.keys()
    )

    seed_oids.add(root_oid)
    seed_oids.update(explicit_oids)

    impacted_by_oid: dict[int, ImpactedObject] = {}

    for seed_oid in sorted(seed_oids):
        seed_object = objects.get(seed_oid)

        if seed_object is None:
            seed_object = get_object(
                db,
                seed_oid,
            )
            objects[seed_oid] = seed_object

        seed_name = (
            f"{seed_object.schema}.{seed_object.name}"
        )

        seed_impact = get_transitive_impact(
            db,
            seed_name,
            set(),
        )

        for impacted in seed_impact:
            if impacted.oid == seed_oid:
                continue

            impacted_by_oid[impacted.oid] = impacted

    # Explicit family objects are also part of the migration set.
    for proposed, oid in resolved:
        object_info = objects.get(oid)

        if object_info is None:
            object_info = get_object(
                db,
                oid,
            )
            objects[oid] = object_info

        impacted_by_oid[oid] = ImpactedObject(
            oid=oid,
            object_name=(
                f"{object_info.schema}.{object_info.name}"
            ),
            object_type=object_info.object_type,
        )

    # Load every PostgreSQL dependent discovered above.
    for impacted in list(
        impacted_by_oid.values()
    ):
        if impacted.oid not in objects:
            objects[impacted.oid] = get_object(
                db,
                impacted.oid,
            )

    migration_oids = set(
        impacted_by_oid.keys()
    )

    migration_oids.update(
        definitions.keys()
    )

    migration_oids.add(
        root_oid
    )

    # Physical tables remain in the snapshot because they are required
    # for dependency analysis and verification.
    for oid in migration_oids:
        if oid not in objects:
            objects[oid] = get_object(
                db,
                oid,
            )

    dependencies = build_family_dependencies(
        db,
        root_oid,
        migration_oids,
        family,
    )

    # Physical tables are externally owned and must NEVER be included
    # in executable family DROP/CREATE order.
    migration_object_oids = {
        oid
        for oid in migration_oids
        if not _is_physical_table(objects[oid])
    }

    # Dependents first for DROP.
    drop_order = _dependency_order_for_oids(
        migration_object_oids,
        dependencies,
        reverse=False,
    )

    # Dependencies first for CREATE.
    create_order = _dependency_order_for_oids(
        migration_object_oids,
        dependencies,
        reverse=True,
    )

    validation = MigrationValidation()

    for change in (
        comparison.added
        + comparison.removed
        + comparison.changed
    ):
        if change.change_type == "REMOVED":
            continue

        if not is_safe_change(change):
            validation.issues.append(
                ValidationIssue(
                    object_name=(
                        f"{root_object.schema}."
                        f"{root_object.name}"
                    ),
                    object_type=root_object.object_type,
                    column_name=change.column_name,
                    issue_type=(
                        change.change_reason
                        or change.change_type
                    ),
                )
            )

    return MigrationPlan(
        root_oid=root_oid,
        root_name=(
            f"{root_object.schema}.{root_object.name}"
        ),
        comparison=comparison,
        is_family=True,
        proposed_definitions=definitions,
        proposed_columns=proposed_columns,
        objects=objects,
        impacted_objects=list(
            impacted_by_oid.values()
        ),
        drop_order=drop_order,
        create_order=create_order,
        dependencies=dependencies,
        validation=validation,
    )


def family_name_from_object_name(
    object_name: str,
) -> str | None:
    """
    Recognize managed family naming conventions:

        t_<family>
        v_<family>
        mv_<family>

    Returns None for arbitrary objects.
    """
    if "." not in object_name:
        return None

    _schema, name = object_name.split(
        ".",
        1,
    )

    if name.startswith("mv_"):
        return name[3:]

    if name.startswith("v_"):
        return name[2:]

    if name.startswith("t_"):
        return name[2:]

    return None


def _get_managed_family_for_proposed(
    db: Database,
    proposed: ProposedObject,
):
    family_name = family_name_from_object_name(
        f"{proposed.schema}.{proposed.name}"
    )

    if family_name is None:
        return None

    try:
        family = get_managed_family(
            db,
            family_name,
        )
    except ValueError:
        return None

    proposed_key = (
        proposed.schema,
        proposed.name,
    )

    family_names = {
        (
            family.source_view_schema,
            family.source_view_name,
        ),
        (
            family.live_table_schema,
            family.live_table_name,
        ),
        (
            family.published_view_schema,
            family.published_view_name,
        ),
    }

    # A managed-family migration requires the exact
    # schema-qualified object to be a registered family member.
    # Object-name prefixes alone (v_, t_, mv_) are not sufficient.
    if proposed_key not in family_names:
        return None

    return family


def build_cli_migration_plan(
    db: Database,
    proposed_objects: list[ProposedObject] | str,
) -> MigrationPlan:
    """
    Build a migration plan from either:

        - raw migration SQL text, or
        - an already-parsed list of ProposedObject instances.

    The CLI/analyzer currently passes raw SQL text, while other
    internal callers may pass parsed proposals.
    """
    if isinstance(proposed_objects, str):
        proposed_objects = [
            parse_proposed_object(proposed_objects)
        ]

    if not proposed_objects:
        raise ValueError(
            "Migration SQL must contain at least one object"
        )

    if len(proposed_objects) == 1:
        proposed = proposed_objects[0]

        family = _get_managed_family_for_proposed(
            db,
            proposed,
        )

        if family is not None:
            family_name = family.name

            return build_family_migration_plan(
                db,
                family_name,
                proposed_objects,
            )

        object_name = (
            f"{proposed.schema}.{proposed.name}"
        )

        return build_migration_plan(
            db,
            object_name,
            proposed_definition=proposed.sql,
        )

    families = []

    for proposed in proposed_objects:
        family = _get_managed_family_for_proposed(
            db,
            proposed,
        )

        if family is None:
            raise ValueError(
                "Multiple migration objects are only supported "
                "for managed families"
            )

        families.append(family)

    family_names = {
        family.name
        for family in families
    }

    if len(family_names) != 1:
        raise ValueError(
            "All migration objects must belong to the same "
            "managed family"
        )

    family_name = next(
        iter(family_names)
    )

    return build_family_migration_plan(
        db,
        family_name,
        proposed_objects,
    )