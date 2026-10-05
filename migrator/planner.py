from .db import Database
from .dependencies import get_dependency_edges, get_dependency_tree
from .models import Dependency


def build_dependencies(db, root):
    rows = get_dependency_tree(db, root)

    if not rows:
        raise ValueError(
            f"Root object not found: {root}"
        )

    root_oid = None

    for oid, depth, object_name, relkind in rows:
        if depth == 0:
            root_oid = oid
            break

    if root_oid is None:
        raise ValueError(
            f"Root object not found: {root}"
        )

    object_oids = {
        oid
        for oid, depth, object_name, relkind in rows
    }

    dependencies = get_dependency_edges(
        db,
        object_oids,
    )

    return root_oid, dependencies   

def get_drop_order(
    root_oid: int,
    dependencies: list[Dependency],
) -> list[int]:
    children = {}

    for dependency in dependencies:
        children.setdefault(
            dependency.referenced_oid,
            set(),
        ).add(
            dependency.dependent_oid
        )

    depth_cache = {}

    def get_depth(
        oid: int,
        path: set[int] | None = None,
    ) -> int:
        if oid in depth_cache:
            return depth_cache[oid]

        if path is None:
            path = set()

        if oid in path:
            raise ValueError(
                "Dependency cycle detected"
            )

        next_path = path | {oid}

        child_oids = children.get(
            oid,
            set(),
        )

        if not child_oids:
            depth_cache[oid] = 0
            return 0

        depth = 1 + max(
            get_depth(
                child_oid,
                next_path,
            )
            for child_oid in child_oids
        )

        depth_cache[oid] = depth

        return depth

    get_depth(root_oid)

    order = []
    visited = set()
    active_path = set()

    def visit(oid: int):
        if oid in active_path:
            raise ValueError(
                "Dependency cycle detected"
            )

        if oid in visited:
            return

        active_path.add(oid)

        dependent_oids = sorted(
            children.get(
                oid,
                set(),
            ),
            key=lambda child_oid: (
                -get_depth(child_oid),
                -child_oid,
            ),
        )

        for dependent_oid in dependent_oids:
            visit(dependent_oid)

        active_path.remove(oid)
        visited.add(oid)
        order.append(oid)

    visit(root_oid)

    return order


def get_create_order(
    root_oid: int,
    dependencies: list[Dependency],
) -> list[int]:
    return list(
        reversed(
            get_drop_order(
                root_oid,
                dependencies,
            )
        )
    )
