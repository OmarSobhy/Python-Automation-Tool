# PostgreSQL View & Table Migration Tool

A Python-based PostgreSQL migration tool for safely analyzing, planning, and applying schema changes to physical tables, views, materialized views, and managed object families.

The tool is designed for controlled database migrations where dependency discovery, migration planning, validation, and rollback are important.

## Features

### Arbitrary PostgreSQL objects

The tool can migrate individual PostgreSQL objects without requiring them to belong to a managed family.

Supported root objects include:

* Physical tables
* Views
* Materialized views

For physical tables, the currently supported schema changes are:

* `ADD COLUMN`
* `DROP COLUMN`

The following changes are intentionally rejected:

* Column type changes
* Nullability changes
* Other unsupported table alterations

Existing physical tables are never dropped and recreated.

### Dependency-aware migrations

The tool uses PostgreSQL's dependency metadata to determine downstream impact.

For example:

```text
auto_views.v_collection_base
        │
        ▼
public.mv_collection_base
        │
        ├── auto_views.v_collection_segmentation_v0
        └── auto_views.v_consumer_delinquency
```

When a change affects a referenced column, dependent views/materialized views can be included in the migration plan.

Objects are dropped and recreated in dependency-safe order.

### Managed object families

Some database objects form a logical family even when PostgreSQL's dependency graph does not completely describe that relationship.

A managed family can contain:

```text
v_<family>     Source/logical view
t_<family>     Live physical table
mv_<family>    Published view
```

For example:

```text
auto_views.v_collection_base
auto_views.t_collection_base
public.mv_collection_base
```

The family is identified through the application's managed-family catalog.

This is separate from PostgreSQL's normal dependency graph.

### Explicit SQL takes precedence

When SQL is supplied for an object, that SQL is authoritative.

For example, if:

```text
v_collection_base.sql
mv_collection_base.sql
```

are both supplied, the explicit `mv_collection_base.sql` is never overwritten by automatic propagation from `v_collection_base.sql`.

Automatic family propagation is only used when an object was not explicitly supplied.

### Safe family propagation

When a live family table changes, the tool can automatically propagate compatible column changes into family views when the view is a simple projection.

For example:

```sql
SELECT
    customer_id,
    loan_id,
    status
FROM auto_views.t_loans_info;
```

can be safely rewritten when the table schema changes.

More complex views are rejected for automatic propagation.

Examples include views containing:

* Joins
* Filters
* Expressions
* Functions
* `CASE`
* `DISTINCT`
* Aggregation
* `GROUP BY`
* Other complex SQL

In those cases, provide explicit:

```text
v_<family>.sql
```

and/or:

```text
mv_<family>.sql
```

definitions.

This avoids making unsafe assumptions about complex SQL.

---

# CLI

## Discover

Discover the migration target and its migration-aware dependency tree:

```powershell
python -m migrator discover <sql_file>
```

Example:

```powershell
python -m migrator discover v_collection_base_changed.sql
```

Typical output:

```text
TARGET: auto_views.v_collection_base [VIEW]
OID: 82159

DEPENDENCY TREE:
auto_views.v_collection_base [VIEW]
└── public.mv_collection_base [VIEW]
    ├── auto_views.v_collection_segmentation_v0 [VIEW]
    └── auto_views.v_consumer_delinquency [VIEW]
```

`discover` uses the same migration-aware dependency information used by the planner.

For managed families, this includes logical family relationships such as:

```text
v_<family> → mv_<family>
```

when both are part of the migration.

---

## Analyze

Analyze the proposed migration and identify:

* Root object
* Object type
* Schema changes
* Impacted objects
* Managed-family status
* Validation issues
* Dependency impact

```powershell
python -m migrator analyze <sql_file>
```

Example:

```powershell
python -m migrator analyze v_collection_base_changed.sql
```

Use `analyze` before generating or applying a migration.

---

## Plan

Generate the SQL migration plan without applying it:

```powershell
python -m migrator plan <sql_file>
```

Example:

```powershell
python -m migrator plan v_collection_base_changed.sql
```

The plan shows the SQL that will be used for the migration.

Review this output before applying the migration.

---

## Apply

Apply the migration:

```powershell
python -m migrator apply <sql_file>
```

Example:

```powershell
python -m migrator apply v_collection_base_changed.sql
```

The migration is executed transactionally.

If migration or verification fails, the transaction is rolled back.

---

# Managed Family Commands

Managed families can also be handled explicitly.

## Plan a family migration

```powershell
python -m migrator plan-family <family_name> <sql_file> [<sql_file> ...]
```

Example:

```powershell
python -m migrator plan-family collection_base v_collection_base_changed.sql
```

Multiple SQL files can be supplied:

```powershell
python -m migrator plan-family collection_base \
    v_collection_base_changed.sql \
    mv_collection_base_changed.sql
```

## Apply a family migration

```powershell
python -m migrator apply-family <family_name> <sql_file> [<sql_file> ...]
```

Example:

```powershell
python -m migrator apply-family collection_base v_collection_base_changed.sql
```

---

# Managed Family Rules

The family migration rules are:

### `v_<family>.sql`

The supplied source-view SQL is authoritative.

If the published view is not explicitly supplied, the tool can generate the published view from the same SELECT definition.

### `mv_<family>.sql`

Explicit SQL always wins.

The tool never replaces an explicitly supplied published-view definition with an automatically generated one.

### `t_<family>.sql`

The proposed table schema can be used to propagate compatible column changes into family views.

The physical table itself is externally owned and is **not dropped or recreated** by the family migration executor.

### Physical family tables

The live table remains in place.

The migration engine does not:

```sql
DROP TABLE
```

or:

```sql
CREATE TABLE
```

for the live managed family table.

---

# Migration Safety

## Physical table changes

Supported:

```sql
ALTER TABLE ... ADD COLUMN ...
ALTER TABLE ... DROP COLUMN ...
```

Not supported:

```sql
ALTER TABLE ... ALTER COLUMN ... TYPE ...
ALTER TABLE ... ALTER COLUMN ... SET NOT NULL
ALTER TABLE ... ALTER COLUMN ... DROP NOT NULL
```

Unsupported changes are reported during validation rather than silently applied.

Adding a `NOT NULL` column without an appropriate default is also rejected because it can fail against existing rows.

---

## Dependency handling

For dependency-sensitive migrations, the tool:

1. Identifies the root object.
2. Discovers dependent objects.
3. Builds the migration dependency graph.
4. Determines safe drop order.
5. Drops dependent views first.
6. Applies the required root change.
7. Recreates objects in dependency order.
8. Verifies the resulting database state.
9. Commits only if the migration succeeds.

If a migration fails, the transaction is rolled back.

---

# Recommended Workflow

For normal migrations, use:

```powershell
python -m migrator discover change.sql
python -m migrator analyze change.sql
python -m migrator plan change.sql
python -m migrator apply change.sql
```

Do not skip the plan review for production changes.

Recommended sequence:

```text
SQL file
   │
   ▼
discover
   │
   ▼
analyze
   │
   ▼
plan
   │
   ▼
human review
   │
   ▼
apply
   │
   ▼
verification
```

---

# Production Usage

This tool is intended for controlled production migrations, not unattended "fire-and-forget" deployments.

Before applying a production migration:

1. Review the SQL input.
2. Run `discover`.
3. Run `analyze`.
4. Run `plan`.
5. Review every affected object.
6. Confirm the proposed SQL.
7. Ensure an appropriate database backup/snapshot exists.
8. Apply the migration.
9. Validate the affected application functionality.

Example:

```powershell
python -m migrator discover production_change.sql
python -m migrator analyze production_change.sql
python -m migrator plan production_change.sql
```

After reviewing the plan:

```powershell
python -m migrator apply production_change.sql
```

---

# Testing

Run the full test suite using the same Python interpreter used to run the application:

```powershell
python -m pytest -q
```

Current baseline:

```text
59 passed
```

Prefer:

```powershell
python -m pytest
```

over:

```powershell
pytest
```

because Windows environments can have multiple Python installations or virtual environments, causing the standalone `pytest` executable to use a different interpreter.

---

# PostgreSQL Configuration

The test configuration can be provided through environment variables.

Typical settings:

```text
PGHOST=localhost
PGPORT=5433
PGDATABASE=testdb
PGUSER=postgres
PGPASSWORD=...
```

The project uses `python-dotenv` for local configuration.

Do not commit production credentials.

---

# Project Structure

A simplified project structure is:

```text
pg-view-migrator/
│
├── migrator/
│   ├── __main__.py
│   ├── models.py
│   ├── db.py
│   ├── snapshot.py
│   ├── dependencies.py
│   ├── impact.py
│   ├── catalog.py
│   ├── proposed.py
│   ├── planner.py
│   ├── migration.py
│   ├── validation.py
│   └── sql_generator.py
│
├── tests/
│
├── README.md
├── RUNBOOK.md
└── requirements.txt
```

---

# Design Principles

The project intentionally favors safety over guessing.

The main principles are:

1. **Explicit SQL wins.**
2. **Physical tables are never casually recreated.**
3. **Unsupported schema changes are rejected.**
4. **PostgreSQL dependencies determine actual downstream impact.**
5. **Managed-family membership is separate from PostgreSQL dependency metadata.**
6. **Automatic SQL propagation is only allowed when it is demonstrably safe.**
7. **Migrations run transactionally.**
8. **Successful execution is followed by verification.**
9. **Production migrations require human review.**

---

# Current Status

The migration engine has been exercised against real PostgreSQL migrations, including:

* Managed-family view migration
* Physical table column removal
* Cross-schema dependency handling
* Dependency-aware view recreation
* Migration verification

The automated test suite currently reports:

```text
59 passed
```

The current implementation should be considered a **controlled production migration tool for the supported migration patterns**, rather than a universal PostgreSQL schema migration framework.
