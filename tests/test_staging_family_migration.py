import pytest

from migrator.catalog import get_managed_family
from migrator.migration import build_family_migration_plan
from migrator.models import ProposedObject


FAMILY_NAME = "loans_sch_info"
PUBLISHED_SCHEMA = "public"


@pytest.fixture
def staging_db(db):
    database_name = db.query(
        "SELECT current_database();"
    )[0][0]

    if database_name != "mylo_local":
        pytest.skip(
            "Staging integration tests require "
            "PGDATABASE=mylo_local"
        )

    return db


def get_family_proposed_objects(staging_db):
    family = get_managed_family(
        staging_db,
        FAMILY_NAME,
        published_schema=PUBLISHED_SCHEMA,
    )

    return [
        ProposedObject(
            schema=family.source_view_schema,
            name=family.source_view_name,
            sql="SELECT 1",
        ),
        ProposedObject(
            schema=family.live_table_schema,
            name=family.live_table_name,
            sql="SELECT 1",
        ),
        ProposedObject(
            schema=family.published_view_schema,
            name=family.published_view_name,
            sql="SELECT 1",
        ),
    ]


def test_staging_family_preserves_actual_object_types(
    staging_db,
):
    proposed_objects = get_family_proposed_objects(
        staging_db
    )

    plan = build_family_migration_plan(
        staging_db,
        FAMILY_NAME,
        proposed_objects,
    )

    family = get_managed_family(
        staging_db,
        FAMILY_NAME,
        published_schema=PUBLISHED_SCHEMA,
    )

    expected_objects = {
        (
            family.source_view_schema,
            family.source_view_name,
        ): "VIEW",
        (
            family.live_table_schema,
            family.live_table_name,
        ): "TABLE",
        (
            family.published_view_schema,
            family.published_view_name,
        ): "VIEW",
    }

    actual_objects = {
        (obj.schema, obj.name): obj.object_type
        for obj in plan.objects.values()
        if (obj.schema, obj.name) in expected_objects
    }

    assert actual_objects == expected_objects


def test_staging_family_discovers_cross_schema_downstream_object(
    staging_db,
):
    proposed_objects = get_family_proposed_objects(
        staging_db
    )

    plan = build_family_migration_plan(
        staging_db,
        FAMILY_NAME,
        proposed_objects,
    )

    family = get_managed_family(
        staging_db,
        FAMILY_NAME,
        published_schema=PUBLISHED_SCHEMA,
    )

    published_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if (
            obj.schema == family.published_view_schema
            and obj.name == family.published_view_name
        )
    )

    assert any(
        dependency.referenced_oid == published_oid
        and dependency.dependent_oid != published_oid
        for dependency in plan.dependencies
    )


def test_staging_family_plan_contains_cross_schema_dependency(
    staging_db,
):
    proposed_objects = get_family_proposed_objects(
        staging_db
    )

    plan = build_family_migration_plan(
        staging_db,
        FAMILY_NAME,
        proposed_objects,
    )

    family = get_managed_family(
        staging_db,
        FAMILY_NAME,
        published_schema=PUBLISHED_SCHEMA,
    )

    public_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if (
            obj.schema == family.published_view_schema
            and obj.name == family.published_view_name
        )
    )

    downstream_oids = [
        dependency.dependent_oid
        for dependency in plan.dependencies
        if dependency.referenced_oid == public_oid
        and dependency.dependent_oid != public_oid
    ]

    assert downstream_oids


def test_staging_family_create_order_respects_cross_schema_dependency(
    staging_db,
):
    proposed_objects = get_family_proposed_objects(
        staging_db
    )

    plan = build_family_migration_plan(
        staging_db,
        FAMILY_NAME,
        proposed_objects,
    )

    family = get_managed_family(
        staging_db,
        FAMILY_NAME,
        published_schema=PUBLISHED_SCHEMA,
    )

    public_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if (
            obj.schema == family.published_view_schema
            and obj.name == family.published_view_name
        )
    )

    downstream_oids = [
        dependency.dependent_oid
        for dependency in plan.dependencies
        if dependency.referenced_oid == public_oid
        and dependency.dependent_oid != public_oid
    ]

    assert downstream_oids

    for downstream_oid in downstream_oids:
        assert (
            plan.create_order.index(public_oid)
            < plan.create_order.index(downstream_oid)
        )


def test_staging_family_drop_order_respects_cross_schema_dependency(
    staging_db,
):
    proposed_objects = get_family_proposed_objects(
        staging_db
    )

    plan = build_family_migration_plan(
        staging_db,
        FAMILY_NAME,
        proposed_objects,
    )

    family = get_managed_family(
        staging_db,
        FAMILY_NAME,
        published_schema=PUBLISHED_SCHEMA,
    )

    public_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if (
            obj.schema == family.published_view_schema
            and obj.name == family.published_view_name
        )
    )

    downstream_oids = [
        dependency.dependent_oid
        for dependency in plan.dependencies
        if dependency.referenced_oid == public_oid
        and dependency.dependent_oid != public_oid
    ]

    assert downstream_oids

    for downstream_oid in downstream_oids:
        assert (
            plan.drop_order.index(downstream_oid)
            < plan.drop_order.index(public_oid)
        )


def test_staging_family_plan_contains_downstream_views(
    staging_db,
):
    proposed_objects = get_family_proposed_objects(
        staging_db
    )

    plan = build_family_migration_plan(
        staging_db,
        FAMILY_NAME,
        proposed_objects,
    )

    object_names = {
        (obj.schema, obj.name)
        for obj in plan.objects.values()
    }

    expected_downstream = {
        ("auto_views", "v_collection_base"),
        ("auto_views", "v_credit_loans"),
        ("auto_views", "v_credit_portfolio"),
        ("auto_views", "v_credit_pending_approval"),
    }

    assert expected_downstream <= object_names


def test_staging_family_plan_never_recreates_physical_table(
    staging_db,
):
    proposed_objects = get_family_proposed_objects(
        staging_db
    )

    plan = build_family_migration_plan(
        staging_db,
        FAMILY_NAME,
        proposed_objects,
    )

    family = get_managed_family(
        staging_db,
        FAMILY_NAME,
        published_schema=PUBLISHED_SCHEMA,
    )

    table_oid = next(
        oid
        for oid, obj in plan.objects.items()
        if (
            obj.schema == family.live_table_schema
            and obj.name == family.live_table_name
        )
    )

    table = plan.objects[table_oid]

    assert table.object_type == "TABLE"

    # The live physical table is externally owned and must
    # never be dropped or recreated by a family migration.
    assert table_oid not in plan.drop_order
    assert table_oid not in plan.create_order
