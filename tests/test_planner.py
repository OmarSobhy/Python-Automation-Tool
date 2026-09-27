from migrator.models import Dependency
from migrator.planner import get_drop_order


def test_get_drop_order_detects_cycle():
    dependencies = [
        Dependency(
            referenced_oid=1,
            dependent_oid=2,
        ),
        Dependency(
            referenced_oid=2,
            dependent_oid=3,
        ),
        Dependency(
            referenced_oid=3,
            dependent_oid=1,
        ),
    ]

    try:
        get_drop_order(
            1,
            dependencies,
        )

        assert False, (
            "Expected dependency cycle to be detected"
        )

    except ValueError as exc:
        assert str(exc) == "Dependency cycle detected"


def test_build_dependencies_identifies_root_by_depth():
    class FakeDatabase:
        pass

    db = FakeDatabase()

    rows = [
        (
            200,
            1,
            "demo.child",
            "v",
        ),
        (
            100,
            0,
            "demo.root",
            "v",
        ),
    ]

    import migrator.planner as planner

    original_get_dependency_tree = (
        planner.get_dependency_tree
    )
    original_get_dependency_edges = (
        planner.get_dependency_edges
    )

    try:
        planner.get_dependency_tree = (
            lambda db, root: rows
        )
        planner.get_dependency_edges = (
            lambda db, object_oids: []
        )

        root_oid, dependencies = (
            planner.build_dependencies(
                db,
                "demo.root",
            )
        )

        assert root_oid == 100
        assert dependencies == []

    finally:
        planner.get_dependency_tree = (
            original_get_dependency_tree
        )
        planner.get_dependency_edges = (
            original_get_dependency_edges
        )

def test_drop_and_create_order_follow_deep_dependency_chain():
    from migrator.models import Dependency
    from migrator.planner import (
        get_create_order,
        get_drop_order,
    )

    # A -> B -> C -> D
    # B depends on A
    # C depends on B
    # D depends on C
    dependencies = [
        Dependency(
            referenced_oid=100,
            dependent_oid=200,
        ),
        Dependency(
            referenced_oid=200,
            dependent_oid=300,
        ),
        Dependency(
            referenced_oid=300,
            dependent_oid=400,
        ),
    ]

    drop_order = get_drop_order(
        100,
        dependencies,
    )

    create_order = get_create_order(
        100,
        dependencies,
    )

    assert drop_order == [
        400,
        300,
        200,
        100,
    ]

    assert create_order == [
        100,
        200,
        300,
        400,
    ]

def test_drop_and_create_order_handle_branching_dependencies():
    from migrator.models import Dependency
    from migrator.planner import (
        get_create_order,
        get_drop_order,
    )

    #        A
    #       / \
    #      B   C
    #      |   |
    #      D   E
    #
    # B and C depend on A.
    # D depends on B.
    # E depends on C.

    dependencies = [
        Dependency(
            referenced_oid=100,
            dependent_oid=200,
        ),
        Dependency(
            referenced_oid=100,
            dependent_oid=300,
        ),
        Dependency(
            referenced_oid=200,
            dependent_oid=400,
        ),
        Dependency(
            referenced_oid=300,
            dependent_oid=500,
        ),
    ]

    drop_order = get_drop_order(
        100,
        dependencies,
    )

    create_order = get_create_order(
        100,
        dependencies,
    )

    assert drop_order.index(400) < drop_order.index(200)
    assert drop_order.index(500) < drop_order.index(300)

    assert drop_order.index(200) < drop_order.index(100)
    assert drop_order.index(300) < drop_order.index(100)

    assert create_order == list(
        reversed(drop_order)
    )