# PostgreSQL Migration Tool Runbook

Operational guide for analyzing, reviewing, applying, and verifying PostgreSQL migrations with the Python migration tool.

---

# 1. Purpose

This runbook defines the standard procedure for running migrations safely.

The tool is designed around:

* PostgreSQL dependency discovery
* Managed object families
* Explicit SQL definitions
* Safe table alterations
* Dependency-aware view recreation
* Transactional execution
* Post-migration verification

The standard rule is:

> Discover first, analyze second, plan third, apply last.

---

# 2. Prerequisites

Before running a migration, confirm:

* Python is installed.
* The project environment is active.
* PostgreSQL is reachable.
* Database credentials are configured.
* The SQL migration file exists.
* You have permission to modify the target database.
* A suitable production backup/snapshot exists when required.

Check the project:

```powershell
cd C:\Users\OmarSobhy\Desktop\pg-view-migrator
```

Verify Python:

```powershell
python --version
```

Verify the application imports:

```powershell
python -c "import migrator; print(migrator.__file__)"
```

---

# 3. Database Configuration

The tool reads PostgreSQL configuration from environment variables.

Typical local configuration:

```text
PGHOST=localhost
PGPORT=5433
PGDATABASE=testdb
PGUSER=postgres
PGPASSWORD=...
```

For production, use the appropriate production values.

Never commit passwords or other database credentials to source control.

---

# 4. Validate the Code Before a Migration

Run the complete test suite:

```powershell
python -m pytest -q
```

Expected current baseline:

```text
59 passed
```

Use `python -m pytest` rather than the standalone `pytest` command when possible.

On Windows, multiple Python installations can cause the standalone `pytest` executable to run under a different interpreter.

---

# 5. Migration Input

A migration SQL file normally contains the desired definition of the target object.

Examples:

```text
v_collection_base_changed.sql
offloading_securitization_changed.sql
```

For a managed family, the filename is not what determines family membership.

The SQL object's schema/name is matched against the managed-family catalog.

For example:

```sql
CREATE OR REPLACE VIEW auto_views.v_collection_base AS
...
```

is resolved as:

```text
collection_base
```

when that object is registered as a managed family member.

---

# 6. Standard Migration Procedure

## Step 1 — Discover

Run:

```powershell
python -m migrator discover change.sql
```

Example:

```powershell
python -m migrator discover v_collection_base_changed.sql
```

Confirm:

* Target schema
* Target object
* Object type
* OID
* Dependency tree

Example:

```text
TARGET: auto_views.v_collection_base [VIEW]
OID: 82159

DEPENDENCY TREE:
auto_views.v_collection_base [VIEW]
└── public.mv_collection_base [VIEW]
    ├── auto_views.v_collection_segmentation_v0 [VIEW]
    └── auto_views.v_consumer_delinquency [VIEW]
```

The dependency tree is migration-aware.

For managed families, the tool can include the logical family relationship:

```text
v_collection_base
        │
        ▼
mv_collection_base
```

even when PostgreSQL's ordinary dependency metadata does not express that relationship in the same way.

---

# 7. Step 2 — Analyze

Run:

```powershell
python -m migrator analyze change.sql
```

Review:

* Migration target
* Migration mode
* Family name, if applicable
* Object type
* Proposed column changes
* Impacted objects
* Validation results
* Dependency relationships

Do not continue if the analysis contains an unexpected object or unsupported change.

---

# 8. Step 3 — Generate the Plan

Run:

```powershell
python -m migrator plan change.sql
```

The plan is the primary artifact to review before execution.

Check:

### Target

Is the correct schema/object being changed?

### Root changes

Are the expected columns being added or removed?

### Impacted objects

Are all affected views/materialized views expected?

### Drop order

Dependents should be dropped before objects they depend on.

### Create order

Referenced objects should be recreated before dependent objects.

### SQL

Confirm that the generated SQL matches the intended migration.

---

# 9. Step 4 — Review

A human should review the plan before applying it to production.

Confirm:

* Correct database
* Correct schema
* Correct root object
* Expected columns
* Expected dependencies
* No unexpected destructive changes
* No unexpected objects
* No unsupported changes
* Correct explicit view definitions
* Correct family propagation

If anything is unexpected:

**Stop. Do not run `apply`.**

Investigate the plan first.

---

# 10. Step 5 — Backup / Snapshot

For production migrations, ensure the appropriate database recovery mechanism exists before applying the change.

Depending on the environment, this may be:

* Database backup
* Snapshot
* Point-in-time recovery
* Replica
* Existing disaster-recovery procedure

The migration itself is transactional, but a transaction is not a replacement for a production backup strategy.

---

# 11. Step 6 — Apply

After review:

```powershell
python -m migrator apply change.sql
```

Example:

```powershell
python -m migrator apply v_collection_base_changed.sql
```

The migration runs inside a transaction.

The expected successful result is:

```text
MIGRATION APPLIED SUCCESSFULLY
MIGRATION VERIFIED
```

---

# 12. Transaction and Rollback Behavior

The migration is designed so that failure does not leave a partially applied dependency migration committed.

If an operation fails:

```text
DROP
ALTER
CREATE
VERIFY
```

the migration is rolled back.

This is particularly important when recreating multiple dependent views.

Do not manually execute individual DROP statements from the generated plan unless you deliberately understand the consequences.

---

# 13. Physical Table Migrations

Physical tables are handled differently from views.

Currently supported:

```text
ADD COLUMN
DROP COLUMN
```

Example:

```sql
ALTER TABLE offloading.securitizations
DROP COLUMN migration_test_2;
```

The tool does not automatically support:

```text
TYPE CHANGE
NULLABILITY CHANGE
```

For example, a proposed change from:

```text
varchar
```

to:

```text
uuid
```

is rejected rather than automatically transformed.

This is intentional.

---

# 14. Managed Family Migrations

A managed family may contain:

```text
v_<family>
t_<family>
mv_<family>
```

For example:

```text
auto_views.v_collection_base
auto_views.t_collection_base
public.mv_collection_base
```

The physical table is considered externally owned.

The family migration executor does not drop or recreate:

```text
t_<family>
```

---

# 15. Source View Migration

When:

```text
v_<family>.sql
```

is supplied, its SQL is authoritative.

If:

```text
mv_<family>
```

is not explicitly supplied, the tool may generate its definition from the same SELECT body.

Example:

```text
v_collection_base.sql
```

can result in:

```text
auto_views.v_collection_base
        │
        ▼
public.mv_collection_base
```

being recreated from the same proposed definition.

---

# 16. Explicit Published View SQL

If:

```text
mv_<family>.sql
```

is supplied explicitly, that definition wins.

For example:

```text
v_collection_base.sql
mv_collection_base.sql
```

means the published-view SQL is taken from:

```text
mv_collection_base.sql
```

It is not overwritten by automatic generation from `v_collection_base.sql`.

This rule is important for production safety.

---

# 17. Automatic Family Propagation

When:

```text
t_<family>.sql
```

changes the proposed table schema, the tool can propagate compatible column changes to family views.

Automatic propagation is only allowed for simple projection views.

Safe example:

```sql
SELECT
    customer_id,
    loan_id,
    status
FROM auto_views.t_loans_info;
```

Unsafe examples include:

```sql
SELECT CASE ...
```

```sql
SELECT ...
FROM a
JOIN b ...
```

```sql
SELECT ...
FROM table
WHERE ...
```

```sql
SELECT DISTINCT ...
```

```sql
SELECT SUM(...) ...
GROUP BY ...
```

When automatic propagation is unsafe, the migration must provide explicit view SQL.

The tool should reject the migration instead of guessing.

---

# 18. Dependency Ordering

The migration engine maintains dependency-safe ordering.

For example:

```text
v_collection_base
        │
        ▼
mv_collection_base
        │
        ▼
v_consumer_delinquency
```

The drop sequence must be approximately:

```text
v_consumer_delinquency
mv_collection_base
v_collection_base
```

The create sequence must be approximately:

```text
v_collection_base
mv_collection_base
v_consumer_delinquency
```

The exact order is calculated from the dependency graph.

---

# 19. Verification

After `apply`, confirm:

* The migration reports success.
* Verification succeeds.
* The root object's definition matches the intended definition.
* Expected columns exist.
* Removed columns no longer exist.
* Dependent views exist.
* Dependent views are valid.
* Materialized views have the expected state.
* Application queries still work.

For important production migrations, perform application-level smoke tests as well.

---

# 20. Managed Family Example

Suppose the current family is:

```text
auto_views.v_collection_base
auto_views.t_collection_base
public.mv_collection_base
```

A source-view change is supplied:

```powershell
python -m migrator discover v_collection_base_changed.sql
```

Then:

```powershell
python -m migrator analyze v_collection_base_changed.sql
```

Then:

```powershell
python -m migrator plan v_collection_base_changed.sql
```

Review the plan.

Finally:

```powershell
python -m migrator apply v_collection_base_changed.sql
```

Expected result:

```text
MIGRATION APPLIED SUCCESSFULLY
MIGRATION VERIFIED
```

---

# 21. Arbitrary Table Example

For an unmanaged physical table:

```text
offloading.securitizations
```

the SQL may propose:

```sql
CREATE TABLE offloading.securitizations (
    securitization_id int4 NOT NULL,
    loan_id uuid NOT NULL,
    batch_id varchar(8000) NOT NULL,
    state varchar(8000) NOT NULL,
    created_at timestamp(6) NOT NULL,
    updated_at timestamp(6) NOT NULL,
    CONSTRAINT securitizations_pkey PRIMARY KEY (securitization_id),
    migration_test varchar
);
```

If the only difference from the live table is an added column, the planner generates an `ALTER TABLE ... ADD COLUMN`.

If a column is removed, the planner can generate:

```sql
ALTER TABLE ...
DROP COLUMN ...
```

The table itself is never dropped and recreated.

---

# 22. Troubleshooting

## `ModuleNotFoundError: No module named 'migrator'`

Use:

```powershell
python -m pytest -q
```

instead of:

```powershell
pytest -q
```

Also verify:

```powershell
python -c "import migrator; print(migrator.__file__)"
```

---

## Published schema is wrong

Managed families can have a published view in a different schema.

For example:

```text
Source:
auto_views.v_collection_base

Published:
public.mv_collection_base
```

The family catalog must contain the correct published schema.

Do not assume the `mv_` object is in the same schema as the source view.

---

## Migration says a family is ambiguous

The managed-family catalog must resolve exactly one family matching the requested name.

Check for duplicate family members.

---

## Automatic propagation is rejected

If the tool reports that automatic family propagation is unsafe, provide explicit:

```text
v_<family>.sql
```

or:

```text
mv_<family>.sql
```

definitions.

Do not simplify or bypass the safety check merely to make the migration run.

---

## Plan contains unexpected dependent objects

Stop before applying.

Run:

```powershell
python -m migrator discover change.sql
```

and inspect the dependency tree.

Then:

```powershell
python -m migrator analyze change.sql
```

Determine why the object is being included.

Unexpected dependency impact should be investigated before production execution.

---

# 23. Production Checklist

Before:

* [ ] Correct database selected
* [ ] Correct SQL file selected
* [ ] Database backup/snapshot available
* [ ] Tests passing
* [ ] `discover` reviewed
* [ ] `analyze` reviewed
* [ ] `plan` reviewed
* [ ] Expected root object confirmed
* [ ] Expected dependencies confirmed
* [ ] Unsupported changes ruled out
* [ ] Explicit SQL reviewed
* [ ] Application impact understood

During:

* [ ] Run `apply`
* [ ] Watch for errors
* [ ] Confirm transaction completes
* [ ] Confirm verification succeeds

After:

* [ ] Confirm `MIGRATION APPLIED SUCCESSFULLY`
* [ ] Confirm `MIGRATION VERIFIED`
* [ ] Verify root object
* [ ] Verify dependent objects
* [ ] Run application smoke tests
* [ ] Monitor for query/application errors

---

# 24. Standard Production Command Sequence

Use:

```powershell
python -m migrator discover change.sql
python -m migrator analyze change.sql
python -m migrator plan change.sql
```

After review:

```powershell
python -m migrator apply change.sql
```

For a managed family:

```powershell
python -m migrator plan-family collection_base v_collection_base_changed.sql
```

Then:

```powershell
python -m migrator apply-family collection_base v_collection_base_changed.sql
```

---

# 25. Operational Principle

The most important rule is:

> Never apply a migration you have not reviewed.

The tool is designed to make migration planning safer and more deterministic, but the final decision remains with the operator.

A successful migration is not just:

```text
SQL executed
```

It is:

```text
Correct target
    +
Correct dependency analysis
    +
Correct migration plan
    +
Successful transaction
    +
Successful verification
    +
Application validation
```

That is the expected production workflow.
