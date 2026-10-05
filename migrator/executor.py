from .db import Database
from .models import (
    DatabaseObject,
    MigrationPlan,
)
from .snapshot import get_object


def execute_migration(
    db: Database,
    plan: MigrationPlan,
    objects: dict[int, DatabaseObject],
    proposed_definition: str | None = None,
) -> None:
    from .sql_generator import (
        generate_create_sql,
        generate_drop_sql,
        generate_family_migration_sql,
        generate_table_alter_sql,
    )

    try:
        db.query("SET LOCAL lock_timeout = '5s';")

        # ==========================================================
        # MANAGED FAMILY MIGRATION
        # ==========================================================
        if plan.is_family:
            _assert_family_physical_tables_protected(plan)

            migration_sql = generate_family_migration_sql(
                plan
            )

            if migration_sql.strip():
                db.query(migration_sql)

            # Verification happens before COMMIT so that any
            # verification failure rolls back the complete migration.
            verify_migration(
                db,
                plan,
                objects,
                proposed_definition=proposed_definition,
            )

            db.connection.commit()
            return

        root = objects[plan.root_oid]

        # ==========================================================
        # 1. DROP DEPENDENT OBJECTS
        # ==========================================================
        #
        # Dependents must be dropped before objects they reference.
        #
        # Physical tables are skipped by generate_drop_sql().
        #
        drop_sql = generate_drop_sql(
            plan.drop_order,
            objects,
        )

        if drop_sql.strip():
            db.query(drop_sql)

        # ==========================================================
        # 2. ALTER PHYSICAL TABLE IN PLACE
        # ==========================================================
        #
        # Physical tables are never dropped/recreated.
        #
        # Supported:
        #   ADD COLUMN
        #   DROP COLUMN
        #
        if root.object_type in {
            "TABLE",
            "PARTITIONED TABLE",
        }:
            alter_sql = generate_table_alter_sql(
                root,
                plan.comparison,
            )

            if alter_sql.strip():
                db.query(alter_sql)

        # ==========================================================
        # 3. RECREATE DEPENDENT OBJECTS
        # ==========================================================
        #
        # Dependencies must be created before objects which depend
        # on them.
        #
        create_sql = generate_create_sql(
            plan.create_order,
            objects,
            root_oid=plan.root_oid,
            proposed_definition=proposed_definition,
            proposed_columns=plan.proposed_columns.get(
                plan.root_oid
            ),
        )

        if create_sql.strip():
            db.query(create_sql)

        # ==========================================================
        # 4. VERIFY BEFORE COMMIT
        # ==========================================================

        verify_migration(
            db,
            plan,
            objects,
            proposed_definition=proposed_definition,
        )

        # ==========================================================
        # 5. COMMIT ONLY AFTER SUCCESSFUL VERIFICATION
        # ==========================================================

        db.connection.commit()

    except Exception:
        db.connection.rollback()
        raise


def execute_family_migration(
    db: Database,
    plan: MigrationPlan,
) -> None:
    """
    Execute a managed-family migration.

    execute_migration() performs verification before commit.
    """
    original_objects = plan.objects

    execute_migration(
        db,
        plan,
        original_objects,
    )


def _assert_family_physical_tables_protected(
    plan: MigrationPlan,
) -> None:
    """
    Hard safety guard.

    A managed-family physical table is externally owned and must
    never be included in executable DROP/CREATE order.
    """
    for oid, obj in plan.objects.items():
        if obj.object_type not in {
            "TABLE",
            "PARTITIONED TABLE",
        }:
            continue

        if oid in plan.drop_order:
            raise RuntimeError(
                "Unsafe family migration plan: physical table "
                f"{obj.schema}.{obj.name} appears in DROP order."
            )

        if oid in plan.create_order:
            raise RuntimeError(
                "Unsafe family migration plan: physical table "
                f"{obj.schema}.{obj.name} appears in CREATE order."
            )


def verify_migration(
    db: Database,
    plan: MigrationPlan,
    original_objects: dict[int, DatabaseObject] | None = None,
    proposed_definition: str | None = None,
) -> None:
    if original_objects is None:
        original_objects = plan.objects

    # =============================================================
    # FAMILY MIGRATION
    # =============================================================
    if plan.is_family:
        family_oids = set(
            plan.proposed_definitions
        )

        # ---------------------------------------------------------
        # 1. Verify family objects.
        # ---------------------------------------------------------
        for oid in family_oids:
            if oid not in original_objects:
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Original object not found for OID {oid}."
                )

            expected_object = original_objects[oid]

            rows = db.query(
                """
                SELECT
                    c.oid,
                    CASE c.relkind
                        WHEN 'r' THEN 'TABLE'
                        WHEN 'p' THEN 'TABLE'
                        WHEN 'v' THEN 'VIEW'
                        WHEN 'm' THEN 'MATERIALIZED VIEW'
                    END AS object_type
                FROM pg_class c
                JOIN pg_namespace n
                    ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relname = %s;
                """,
                (
                    expected_object.schema,
                    expected_object.name,
                ),
            )

            if not rows:
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Object does not exist: "
                    f"{expected_object.schema}."
                    f"{expected_object.name}"
                )

            actual_oid, actual_type = rows[0]

            if actual_type != expected_object.object_type:
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Object type changed for "
                    f"{expected_object.schema}."
                    f"{expected_object.name}: "
                    f"expected {expected_object.object_type}, "
                    f"got {actual_type}."
                )

            actual_object = get_object(
                db,
                actual_oid,
            )

            # -----------------------------------------------------
            # Physical tables are verified through columns.
            # -----------------------------------------------------
            if expected_object.object_type in {
                "TABLE",
                "PARTITIONED TABLE",
            }:
                from .proposed import get_proposed_columns

                proposed_sql = plan.proposed_definitions[oid]

                proposed_columns = get_proposed_columns(
                    db,
                    proposed_sql,
                    object_type="TABLE",
                )

                expected_columns = {
                    column.name: column
                    for column in proposed_columns
                }

                actual_columns = {
                    column.name: column
                    for column in actual_object.columns
                }

                expected_names = set(
                    expected_columns
                )
                actual_names = set(
                    actual_columns
                )

                if expected_names != actual_names:
                    raise RuntimeError(
                        "Migration verification failed. "
                        f"Column names mismatch for "
                        f"{expected_object.schema}."
                        f"{expected_object.name}: "
                        f"expected "
                        f"{sorted(expected_names)}, "
                        f"got "
                        f"{sorted(actual_names)}."
                    )

                for name, expected_column in (
                    expected_columns.items()
                ):
                    actual_column = actual_columns[name]

                    if (
                        expected_column.data_type
                        != actual_column.data_type
                    ):
                        raise RuntimeError(
                            "Migration verification failed. "
                            f"Column type mismatch for "
                            f"{expected_object.schema}."
                            f"{expected_object.name}."
                            f"{name}: "
                            f"expected "
                            f"'{expected_column.data_type}', "
                            f"got "
                            f"'{actual_column.data_type}'."
                        )

                    if (
                        expected_column.nullable
                        != actual_column.nullable
                    ):
                        raise RuntimeError(
                            "Migration verification failed. "
                            f"Column nullability mismatch for "
                            f"{expected_object.schema}."
                            f"{expected_object.name}."
                            f"{name}."
                        )

            # -----------------------------------------------------
            # Views/MVs retain their proposed definitions.
            # -----------------------------------------------------
            else:
                verify_definition(
                    (
                        f"{expected_object.schema}."
                        f"{expected_object.name}"
                    ),
                    plan.proposed_definitions[oid],
                    actual_object.definition,
                    db,
                )

            # -----------------------------------------------------
            # Materialized-view population state.
            # -----------------------------------------------------
            if (
                expected_object.object_type
                == "MATERIALIZED VIEW"
                and (
                    expected_object.materialized_view_populated
                    != actual_object.materialized_view_populated
                )
            ):
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Materialized view population state changed "
                    f"for "
                    f"{expected_object.schema}."
                    f"{expected_object.name}."
                )

        # ---------------------------------------------------------
        # 2. Verify dependency objects.
        # ---------------------------------------------------------
        dependency_oids = (
            set(original_objects) - family_oids
        )

        for oid in dependency_oids:
            expected_object = original_objects[oid]

            rows = db.query(
                """
                SELECT
                    c.oid,
                    CASE c.relkind
                        WHEN 'r' THEN 'TABLE'
                        WHEN 'p' THEN 'TABLE'
                        WHEN 'v' THEN 'VIEW'
                        WHEN 'm' THEN 'MATERIALIZED VIEW'
                    END AS object_type
                FROM pg_class c
                JOIN pg_namespace n
                    ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relname = %s;
                """,
                (
                    expected_object.schema,
                    expected_object.name,
                ),
            )

            if not rows:
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Dependency object does not exist: "
                    f"{expected_object.schema}."
                    f"{expected_object.name}"
                )

            actual_oid, actual_type = rows[0]

            if actual_type != expected_object.object_type:
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Dependency object type changed for "
                    f"{expected_object.schema}."
                    f"{expected_object.name}: "
                    f"expected {expected_object.object_type}, "
                    f"got {actual_type}."
                )

            actual_object = get_object(
                db,
                actual_oid,
            )

            if (
                expected_object.object_type
                in {
                    "VIEW",
                    "MATERIALIZED VIEW",
                }
                and expected_object.definition
            ):
                verify_definition(
                    (
                        f"{expected_object.schema}."
                        f"{expected_object.name}"
                    ),
                    expected_object.definition,
                    actual_object.definition,
                    db,
                )

            if (
                expected_object.object_type
                == "MATERIALIZED VIEW"
                and (
                    expected_object.materialized_view_populated
                    != actual_object.materialized_view_populated
                )
            ):
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Materialized view population state changed "
                    f"for dependency object "
                    f"{expected_object.schema}."
                    f"{expected_object.name}."
                )

        # ---------------------------------------------------------
        # 3. Verify dependency relationships.
        # ---------------------------------------------------------
        #
        # Important family rule:
        #
        # PostgreSQL dependency edges between members of a managed
        # family are NOT necessarily stable across a family rebuild.
        #
        # Example:
        #
        #   OLD:
        #       t_orders -> v_orders
        #       v_orders -> mv_orders
        #       mv_orders -> v_order_report
        #
        #   NEW canonical family:
        #       t_orders -> v_orders
        #       t_orders -> mv_orders
        #       mv_orders -> v_order_report
        #
        # v_orders is canonical, but mv_orders is generated from the
        # same SELECT body. Therefore v_orders -> mv_orders is not a
        # required post-migration edge.
        #
        # We therefore verify:
        #
        #   * all dependency edges involving a non-family downstream
        #     object;
        #   * but do not require obsolete edges whose two endpoints
        #     are both managed-family objects.
        #
        recreated_oids = set(
            original_objects
        )

        family_internal_edges = set()

        for (
            referenced_old_oid,
            dependent_old_oid,
        ) in _get_family_internal_edges(
            plan,
            recreated_oids,
        ):
            family_internal_edges.add(
                (
                    referenced_old_oid,
                    dependent_old_oid,
                )
            )

        expected_edges = {
            (
                dependency.referenced_oid,
                dependency.dependent_oid,
            )
            for dependency in plan.dependencies
            if (
                dependency.referenced_oid
                in recreated_oids
                and dependency.dependent_oid
                in recreated_oids
                and (
                    dependency.referenced_oid,
                    dependency.dependent_oid,
                )
                not in family_internal_edges
            )
        }

        current_oid_map: dict[int, int] = {}

        for old_oid, expected_object in (
            original_objects.items()
        ):
            rows = db.query(
                """
                SELECT c.oid
                FROM pg_class c
                JOIN pg_namespace n
                    ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relname = %s;
                """,
                (
                    expected_object.schema,
                    expected_object.name,
                ),
            )

            if not rows:
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Could not remap recreated object: "
                    f"{expected_object.schema}."
                    f"{expected_object.name}"
                )

            current_oid_map[old_oid] = rows[0][0]

        missing_edges = []

        for (
            referenced_old_oid,
            dependent_old_oid,
        ) in expected_edges:
            referenced_new_oid = current_oid_map[
                referenced_old_oid
            ]

            dependent_new_oid = current_oid_map[
                dependent_old_oid
            ]

            rows = db.query(
                """
                SELECT 1
                FROM pg_depend d
                JOIN pg_rewrite rw
                    ON rw.oid = d.objid
                JOIN pg_class dependent
                    ON dependent.oid = rw.ev_class
                WHERE d.refobjid = %s
                  AND rw.ev_class = %s
                  AND dependent.relkind IN ('v', 'm')
                LIMIT 1;
                """,
                (
                    referenced_new_oid,
                    dependent_new_oid,
                ),
            )

            if not rows:
                referenced = original_objects[
                    referenced_old_oid
                ]

                dependent = original_objects[
                    dependent_old_oid
                ]

                missing_edges.append(
                    (
                        f"{referenced.schema}."
                        f"{referenced.name}"
                        " -> "
                        f"{dependent.schema}."
                        f"{dependent.name}"
                    )
                )

        if missing_edges:
            raise RuntimeError(
                "Migration verification failed. "
                "Missing dependency relationships: "
                f"{sorted(missing_edges)}"
            )

        # ---------------------------------------------------------
        # 4. Verify metadata.
        # ---------------------------------------------------------
        verify_object_metadata(
            original_objects,
            db,
            proposed_definitions=plan.proposed_definitions,
        )

        return

    # =============================================================
    # NORMAL SINGLE-OBJECT MIGRATION
    # =============================================================

    old_root = original_objects[plan.root_oid]

    rows = db.query(
        """
        SELECT c.oid
        FROM pg_class c
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relname = %s;
        """,
        (
            old_root.schema,
            old_root.name,
        ),
    )

    if not rows:
        raise RuntimeError(
            "Migration verification failed. "
            f"Object does not exist: "
            f"{old_root.schema}.{old_root.name}"
        )

    new_root_oid = rows[0][0]

    new_root = get_object(
        db,
        new_root_oid,
    )

    # Verify every object captured in the plan still exists and has
    # the same PostgreSQL object type.
    verify_objects(
        db,
        original_objects,
    )

    # Verify the root schema after ADD/DROP operations.
    verify_columns(
        old_root,
        new_root,
        plan,
    )

    # Verify downstream dependency objects.
    verify_dependency_chain(
        db,
        plan,
        original_objects,
    )

    expected_definition = proposed_definition

    if expected_definition is None:
        expected_definition = plan.proposed_definitions.get(
            plan.root_oid
        )

    # Physical tables do not have a CREATE VIEW-style definition.
    if new_root.object_type not in {
        "TABLE",
        "PARTITIONED TABLE",
    }:
        if expected_definition is not None:
            verify_definition(
                f"{new_root.schema}.{new_root.name}",
                expected_definition,
                new_root.definition,
                db,
            )

    verify_object_metadata(
        original_objects,
        db,
        root_oid=plan.root_oid,
        proposed_definition=expected_definition,
    )


def _get_family_internal_edges(
    plan: MigrationPlan,
    recreated_oids: set[int],
) -> set[tuple[int, int]]:
    """
    Return dependency edges where both endpoints are managed-family
    objects.

    These edges are intentionally excluded from strict verification.

    Family relationships are logical relationships and can change when
    canonical v_<family> SQL is propagated to mv_<family>.
    """
    family_oids = set(
        plan.proposed_definitions
    )

    return {
        (
            dependency.referenced_oid,
            dependency.dependent_oid,
        )
        for dependency in plan.dependencies
        if (
            dependency.referenced_oid in recreated_oids
            and dependency.dependent_oid in recreated_oids
            and dependency.referenced_oid in family_oids
            and dependency.dependent_oid in family_oids
        )
    }


def verify_family_object(
    db: Database,
    expected: DatabaseObject,
    proposed_definition: str,
) -> None:
    object_name = (
        f"{expected.schema}.{expected.name}"
    )

    rows = db.query(
        """
        SELECT
            c.oid,
            CASE c.relkind
                WHEN 'r' THEN 'TABLE'
                WHEN 'p' THEN 'TABLE'
                WHEN 'v' THEN 'VIEW'
                WHEN 'm' THEN 'MATERIALIZED VIEW'
            END AS object_type
        FROM pg_class c
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relname = %s;
        """,
        (
            expected.schema,
            expected.name,
        ),
    )

    if not rows:
        raise RuntimeError(
            "Migration verification failed. "
            f"Object does not exist: {object_name}"
        )

    actual_oid, actual_type = rows[0]

    if actual_type != expected.object_type:
        raise RuntimeError(
            "Migration verification failed. "
            f"Object type mismatch for {object_name}: "
            f"expected '{expected.object_type}', "
            f"got '{actual_type}'."
        )

    actual_object = get_object(
        db,
        actual_oid,
    )

    if expected.object_type not in {
        "TABLE",
        "PARTITIONED TABLE",
    }:
        verify_definition(
            object_name,
            proposed_definition,
            actual_object.definition,
            db,
        )


def verify_objects(
    db: Database,
    original_objects: dict[int, DatabaseObject],
) -> None:
    for expected in original_objects.values():
        object_name = (
            f"{expected.schema}.{expected.name}"
        )

        rows = db.query(
            """
            SELECT
                c.oid,
                CASE c.relkind
                    WHEN 'r' THEN 'TABLE'
                    WHEN 'p' THEN 'TABLE'
                    WHEN 'v' THEN 'VIEW'
                    WHEN 'm' THEN 'MATERIALIZED VIEW'
                END AS object_type
            FROM pg_class c
            JOIN pg_namespace n
                ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = %s;
            """,
            (
                expected.schema,
                expected.name,
            ),
        )

        if not rows:
            raise RuntimeError(
                "Migration verification failed. "
                f"Object does not exist: {object_name}"
            )

        _, actual_type = rows[0]

        if actual_type != expected.object_type:
            raise RuntimeError(
                "Migration verification failed. "
                f"Object type mismatch for {object_name}: "
                f"expected '{expected.object_type}', "
                f"got '{actual_type}'."
            )


def verify_columns(
    old_root: DatabaseObject,
    new_root: DatabaseObject,
    plan: MigrationPlan,
) -> None:
    expected_columns = {
        column.name: column
        for column in old_root.columns
    }

    # Additions.
    for change in plan.comparison.added:
        if change.new_column is not None:
            expected_columns[
                change.column_name
            ] = change.new_column

    # Removals.
    for change in plan.comparison.removed:
        expected_columns.pop(
            change.column_name,
            None,
        )

    # Changed columns are included here for verification even though
    # automatic TYPE/NULLABILITY changes are currently rejected.
    for change in plan.comparison.changed:
        if change.new_column is not None:
            expected_columns[
                change.column_name
            ] = change.new_column

    actual_columns = {
        column.name: column
        for column in new_root.columns
    }

    expected_names = set(
        expected_columns
    )
    actual_names = set(
        actual_columns
    )

    if expected_names != actual_names:
        raise RuntimeError(
            "Migration verification failed. "
            f"Column names mismatch for "
            f"{new_root.schema}.{new_root.name}: "
            f"expected {sorted(expected_names)}, "
            f"got {sorted(actual_names)}."
        )

    for name, expected in expected_columns.items():
        actual = actual_columns[name]

        if expected.data_type != actual.data_type:
            raise RuntimeError(
                "Migration verification failed. "
                f"Column type mismatch for "
                f"{new_root.schema}.{new_root.name}.{name}: "
                f"expected '{expected.data_type}', "
                f"got '{actual.data_type}'."
            )

        if expected.nullable != actual.nullable:
            raise RuntimeError(
                "Migration verification failed. "
                f"Column nullability mismatch for "
                f"{new_root.schema}.{new_root.name}.{name}."
            )


def verify_dependency_chain(
    db: Database,
    plan: MigrationPlan,
    original_objects: dict[int, DatabaseObject],
) -> None:
    """
    Verify that every expected downstream dependency object still
    exists after recreation.

    PostgreSQL assigns new OIDs after DROP/CREATE, so this is resolved
    using schema/name rather than the old OID.
    """

    root = original_objects[plan.root_oid]

    # -------------------------------------------------------------
    # 1. Every planned object must exist.
    # -------------------------------------------------------------
    for expected in original_objects.values():
        rows = db.query(
            """
            SELECT
                c.oid,
                CASE c.relkind
                    WHEN 'r' THEN 'TABLE'
                    WHEN 'p' THEN 'TABLE'
                    WHEN 'v' THEN 'VIEW'
                    WHEN 'm' THEN 'MATERIALIZED VIEW'
                END AS object_type
            FROM pg_class c
            JOIN pg_namespace n
                ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = %s;
            """,
            (
                expected.schema,
                expected.name,
            ),
        )

        if not rows:
            raise RuntimeError(
                "Migration verification failed. "
                f"Missing dependency object: "
                f"{expected.schema}.{expected.name}"
            )

        _, actual_type = rows[0]

        if actual_type != expected.object_type:
            raise RuntimeError(
                "Migration verification failed. "
                f"Dependency object type changed for "
                f"{expected.schema}.{expected.name}: "
                f"expected {expected.object_type}, "
                f"got {actual_type}."
            )

    # -------------------------------------------------------------
    # 2. Resolve recreated root.
    # -------------------------------------------------------------
    root_rows = db.query(
        """
        SELECT c.oid
        FROM pg_class c
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relname = %s;
        """,
        (
            root.schema,
            root.name,
        ),
    )

    if not root_rows:
        raise RuntimeError(
            "Migration verification failed. "
            f"Migration root does not exist: "
            f"{root.schema}.{root.name}"
        )

    new_root_oid = root_rows[0][0]

    # -------------------------------------------------------------
    # 3. Rebuild actual downstream dependency tree.
    # -------------------------------------------------------------
    rows = db.query(
        """
        WITH RECURSIVE dependency_tree AS (
            SELECT
                c.oid,
                n.nspname,
                c.relname,
                c.relkind,
                ARRAY[c.oid]::oid[] AS path
            FROM pg_class c
            JOIN pg_namespace n
                ON n.oid = c.relnamespace
            WHERE c.oid = %s

            UNION ALL

            SELECT
                dependent.oid,
                dependent_ns.nspname,
                dependent.relname,
                dependent.relkind,
                dt.path || dependent.oid
            FROM dependency_tree dt
            JOIN pg_depend d
                ON d.refobjid = dt.oid
            JOIN pg_rewrite rw
                ON rw.oid = d.objid
            JOIN pg_class dependent
                ON dependent.oid = rw.ev_class
            JOIN pg_namespace dependent_ns
                ON dependent_ns.oid =
                    dependent.relnamespace
            WHERE dependent.relkind IN ('v', 'm')
              AND NOT dependent.oid = ANY(dt.path)
        )
        SELECT DISTINCT
            nspname,
            relname
        FROM dependency_tree;
        """,
        (new_root_oid,),
    )

    actual_downstream_names = {
        f"{schema}.{name}"
        for schema, name in rows
    }

    expected_root_name = (
        f"{root.schema}.{root.name}"
    )

    expected_downstream_names = {
        f"{obj.schema}.{obj.name}"
        for obj in original_objects.values()
        if obj.oid != plan.root_oid
    }

    missing_downstream = (
        expected_downstream_names
        - actual_downstream_names
    )

    if missing_downstream:
        raise RuntimeError(
            "Migration verification failed. "
            "Missing downstream dependency objects: "
            f"{sorted(missing_downstream)}"
        )

    if expected_root_name not in actual_downstream_names:
        raise RuntimeError(
            "Migration verification failed. "
            f"Migration root missing from dependency tree: "
            f"{expected_root_name}"
        )


def verify_family_dependency_chain(
    db: Database,
    plan: MigrationPlan,
    original_objects: dict[int, DatabaseObject],
) -> None:
    """
    Legacy family dependency helper.

    Kept for compatibility with callers/tests.

    The authoritative family dependency verification is now performed
    directly by verify_migration(), because family-internal dependency
    edges are intentionally allowed to change.
    """
    family_oids = set(
        plan.proposed_definitions
    )

    for oid in family_oids:
        if oid not in original_objects:
            continue

        expected = original_objects[oid]

        rows = db.query(
            """
            SELECT c.oid
            FROM pg_class c
            JOIN pg_namespace n
                ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = %s;
            """,
            (
                expected.schema,
                expected.name,
            ),
        )

        if not rows:
            raise RuntimeError(
                "Migration verification failed. "
                f"Family object does not exist: "
                f"{expected.schema}.{expected.name}"
            )


def verify_object_metadata(
    objects: dict[int, DatabaseObject],
    db: Database,
    root_oid: int | None = None,
    proposed_definition: str | None = None,
    proposed_definitions: dict[int, str] | None = None,
) -> None:
    """
    Verify metadata for recreated objects.

    Includes:
      - owner
      - comment
      - materialized-view population state
      - index definitions
    """

    for expected in objects.values():
        object_name = (
            f"{expected.schema}.{expected.name}"
        )

        rows = db.query(
            """
            SELECT
                c.oid,
                pg_get_userbyid(c.relowner),
                obj_description(c.oid, 'pg_class'),
                CASE
                    WHEN c.relkind = 'm'
                    THEN c.relispopulated
                    ELSE NULL
                END
            FROM pg_class c
            JOIN pg_namespace n
                ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relname = %s;
            """,
            (
                expected.schema,
                expected.name,
            ),
        )

        if not rows:
            raise RuntimeError(
                "Migration verification failed. "
                f"Object does not exist: {object_name}"
            )

        (
            actual_oid,
            actual_owner,
            actual_comment,
            actual_populated,
        ) = rows[0]

        # ---------------------------------------------------------
        # Owner
        # ---------------------------------------------------------
        if expected.owner != actual_owner:
            raise RuntimeError(
                "Migration verification failed. "
                f"Owner mismatch for {object_name}: "
                f"expected '{expected.owner}', "
                f"got '{actual_owner}'."
            )

        # ---------------------------------------------------------
        # Comment
        # ---------------------------------------------------------
        if expected.comment != actual_comment:
            raise RuntimeError(
                "Migration verification failed. "
                f"Comment mismatch for {object_name}."
            )

        # ---------------------------------------------------------
        # Materialized-view population state
        # ---------------------------------------------------------
        if expected.materialized_view_populated is not None:
            if (
                expected.materialized_view_populated
                != actual_populated
            ):
                raise RuntimeError(
                    "Migration verification failed. "
                    f"Materialized view population state "
                    f"mismatch for {object_name}."
                )

        # ---------------------------------------------------------
        # Indexes
        # ---------------------------------------------------------
        actual_object = get_object(
            db,
            actual_oid,
        )

        expected_indexes = {
            index.definition.rstrip().rstrip(";")
            for index in expected.indexes
        }

        actual_indexes = {
            index.definition.rstrip().rstrip(";")
            for index in actual_object.indexes
        }

        if expected_indexes != actual_indexes:
            raise RuntimeError(
                "Migration verification failed. "
                f"Index definitions mismatch for "
                f"{object_name}."
            )


def verify_definition(
    object_name: str,
    expected_definition: str,
    actual_definition: str | None,
    db: Database,
) -> None:
    if actual_definition is None:
        raise RuntimeError(
            "Migration verification failed. "
            f"Object has no definition: "
            f"{object_name}"
        )

    normalized_expected = (
        expected_definition
        .strip()
        .rstrip(";")
    )

    temp_view_name = (
        "__pg_view_migrator_verify"
    )

    try:
        db.query(
            f"""
            CREATE TEMP VIEW {temp_view_name} AS
            {normalized_expected};
            """
        )

        rows = db.query(
            """
            SELECT pg_get_viewdef(
                %s::regclass,
                true
            );
            """,
            (temp_view_name,),
        )

        if not rows:
            raise RuntimeError(
                "Migration verification failed. "
                f"Could not normalize definition "
                f"for {object_name}"
            )

        expected_normalized = (
            rows[0][0]
            .strip()
            .rstrip(";")
        )

    finally:
        db.query(
            f"DROP VIEW IF EXISTS {temp_view_name};"
        )

    actual_normalized = (
        actual_definition
        .strip()
        .rstrip(";")
    )

    if expected_normalized != actual_normalized:
        raise RuntimeError(
            "Migration verification failed. "
            f"Definition mismatch for "
            f"{object_name}"
        )