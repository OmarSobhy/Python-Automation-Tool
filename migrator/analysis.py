from .migration import build_cli_migration_plan


def analyze_migration(
    db,
    sql,
):
    plan = build_cli_migration_plan(
        db,
        sql,
    )

    column_changes = (
        plan.comparison.added
        + plan.comparison.removed
        + plan.comparison.changed
    )

    return {
        "root_name": plan.root_name,
        "root_oid": plan.root_oid,
        "breaking": plan.comparison.is_breaking,
        "column_changes": column_changes,
        "impacted_objects": plan.impacted_objects,
        "dependencies": plan.dependencies,
        "plan": plan,
    }