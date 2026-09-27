# pg-view-migrator

A PostgreSQL migration tool for safely changing SQL-backed database objects while preserving their existing PostgreSQL types, dependencies, indexes, ownership, comments, and other catalog metadata.

The tool is designed around **dependency-aware migrations** and supports migrating a managed family of related objects such as:

```text
v_loans_sch_info
t_loans_sch_info
mv_loans_sch_info
```

The SQL naming prefix (`v_`, `t_`, `mv_`) is a naming convention only. The actual PostgreSQL object type in the database is authoritative.

---

## What it does

`pg-view-migrator` can:

* Discover managed object families.
* Resolve migration SQL files to existing database objects.
* Preserve the actual PostgreSQL object type.
* Detect and follow PostgreSQL view/materialized-view dependencies.
* Drop dependent objects before their referenced objects.
* Recreate objects in dependency order.
* Apply new SQL only to the explicitly migrated family objects.
* Recreate downstream objects using their existing definitions.
* Preserve indexes, ownership, comments, and materialized-view population state.
* Recreate tables without data using `WITH NO DATA`.
* Verify the resulting objects and dependency relationships after migration.

The intended workflow is:

```text
SQL files
   ↓
Discover family
   ↓
Build migration plan
   ↓
Review plan
   ↓
Apply migration
   ↓
Verify database state
```

---

## Supported object types

The migration engine supports:

* TABLE
* VIEW
* MATERIALIZED VIEW

The existing PostgreSQL object type is preserved.

For example, if a file is named:

```text
mv_loans_sch_info.sql
```

but the existing object is actually:

```text
public.mv_loans_sch_info
```

with PostgreSQL type `VIEW`, the migration recreates it as a `VIEW`.

It does **not** convert it to a materialized view merely because its name starts with `mv_`.

---

## Migration SQL

Migration SQL files contain the SQL/query that defines the object.

For example:

```sql
CREATE VIEW public.mv_loans_sch_info AS
SELECT
    loan_schedule_id,
    loan_id,
    consumer_id
FROM auto_views.t_loans_sch_info;
```

The migration engine extracts the query definition and combines it with the actual object metadata from PostgreSQL.

For an existing table, the resulting migration uses:

```sql
CREATE TABLE schema.table AS
<query>
WITH NO DATA;
```

This means the migration recreates the table structure but does not copy data.

---

## Managed object families

A managed family groups related objects by logical name.

For example:

```text
loans_sch_info
```

can correspond to:

```text
auto_views.v_loans_sch_info
auto_views.t_loans_sch_info
public.mv_loans_sch_info
```

The family is resolved from the database catalog.

If multiple matching objects make the family ambiguous, the migration is rejected rather than guessing.

A published-view schema can also be supplied through the schema-qualified migration SQL.

---

## Dependency handling

Before migration, the tool discovers dependent PostgreSQL views and materialized views.

Objects are dropped in dependency-safe order:

```text
dependent objects
        ↓
referenced objects
```

They are recreated in the opposite direction:

```text
referenced objects
        ↓
dependent objects
```

Downstream objects that are not part of the proposed family are recreated from their **existing database definitions**.

For example:

```text
v_loans_sch_info       ← changed
        ↓
v_collection_base      ← existing definition
        ↓
v_merchant_branch_info ← existing definition
```

Only the explicitly supplied family SQL receives the new SQL definition.

---

## Schema changes

Schema changes are supported.

For example, a migration can add a new column to the family SQL:

```sql
'migration_schema_test' AS migration_schema_test
```

The affected family objects are recreated using the new definition.

Downstream objects retain their existing SQL definitions unless they are explicitly part of the migration family.

After the migration, verification checks that the resulting objects match the proposed family definitions and that captured dependency relationships still exist.

---

## Metadata preservation

The migration captures and restores relevant existing database metadata, including:

* Object ownership
* Object comments
* Index definitions
* Materialized-view populated/unpopulated state

Indexes are restored after the object has been recreated.

The migration does not infer metadata from object-name prefixes.

---

# Installation

The project uses Python and PostgreSQL.

Install the Python dependencies:

```powershell
pip install -r requirements.txt
```

The project expects PostgreSQL to be reachable using the environment variables described below.

---

# Configuration

Create a `.env` file in the project root.

Example:

```env
PGHOST=localhost
PGPORT=5433
PGDATABASE=mylo_local
PGUSER=postgres
PGPASSWORD=YOUR_POSTGRES_PASSWORD
```

The database can be changed by modifying:

```env
PGDATABASE=...
```

For example:

```env
PGDATABASE=mylo_local
```

for the staging/rehearsal database.

---

# Discovering a family

Use:

```powershell
python -m migrator discover loans_sch_info
```

This is useful before creating a migration plan.

It shows the objects discovered for the logical family and helps identify schema ambiguity.

---

# Planning a family migration

Given:

```text
t_loans_sch_info.sql
v_loans_sch_info.sql
mv_loans_sch_info.sql
```

run:

```powershell
python -m migrator plan-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

The plan shows:

* Proposed objects
* Existing object types
* Objects receiving new SQL
* Objects retaining existing SQL
* Drop order
* Create order
* Index restoration
* Metadata restoration

Example:

```text
PROPOSED OBJECTS
----------------
auto_views.t_loans_sch_info
auto_views.v_loans_sch_info
public.mv_loans_sch_info
```

The plan should always be reviewed before applying a migration.

---

# Applying a family migration

After reviewing the plan:

```powershell
python -m migrator apply-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

A successful migration ends with:

```text
FAMILY MIGRATION APPLIED SUCCESSFULLY
VERIFICATION PASSED
```

If migration execution fails, the transaction is rolled back.

---

# Recommended workflow

Use this workflow for a real migration:

### 1. Prepare SQL files

Prepare the SQL definitions for the objects being intentionally changed.

### 2. Point the environment at the rehearsal database

For example:

```env
PGDATABASE=mylo_local
```

### 3. Discover the family

```powershell
python -m migrator discover loans_sch_info
```

### 4. Generate the plan

```powershell
python -m migrator plan-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

### 5. Review the plan

Confirm:

* The intended objects are `NEW SQL`.
* Unchanged downstream objects are `EXISTING SQL`.
* Object types are correct.
* Drop order is dependency-safe.
* Create order is dependency-safe.
* The expected indexes and metadata are preserved.

### 6. Apply

```powershell
python -m migrator apply-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

### 7. Verify

Do not consider the migration successful unless:

```text
VERIFICATION PASSED
```

is reported.

---

# Testing

Run the complete test suite:

```powershell
python -m pytest -q
```

Run the staging integration tests:

```powershell
python -m pytest tests/test_staging_family_migration.py -q
```

The staging integration tests require:

```env
PGDATABASE=mylo_local
```

Otherwise they are skipped.

---

# Rehearsal database

The project can be tested against a schema-only clone of the staging database.

This is useful because it exercises:

* Real PostgreSQL catalog structure
* Real schemas
* Real object types
* Real cross-schema dependencies
* Real indexes
* Real downstream views
* Real dependency ordering

No production data is required for the schema rehearsal.

---

# Important behavior

## Existing object type is authoritative

The SQL filename does not determine the PostgreSQL object type.

For example:

```text
mv_example.sql
```

does not automatically mean:

```sql
CREATE MATERIALIZED VIEW
```

The migration uses the existing catalog object type.

---

## Family SQL versus downstream SQL

If an object is explicitly supplied as migration input:

```text
NEW SQL
```

is used.

If an object is only discovered as a dependency:

```text
EXISTING SQL
```

is used.

This prevents a migration from unintentionally replacing downstream SQL definitions.

---

## Tables contain no data after recreation

Tables are recreated with:

```sql
WITH NO DATA
```

The migration tool is intended to recreate table structure, not migrate table contents.

Any required data population is expected to happen separately.

---

## Materialized views

Materialized views preserve their previous populated state.

If an existing materialized view was unpopulated, it is recreated using:

```sql
WITH NO DATA
```

---

## Dependency limitations

PostgreSQL's dependency catalog records database-level dependencies.

External systems that populate or manipulate tables may have relationships that PostgreSQL cannot discover.

For example, an external workflow may populate a table from a view without PostgreSQL recording a dependency from the table to that view.

Those external dependencies must therefore be handled outside the database dependency graph.

---

# Safety expectations

Before applying a migration:

1. Review the generated plan.
2. Confirm the target database.
3. Confirm the proposed object list.
4. Confirm the `NEW SQL` / `EXISTING SQL` classification.
5. Review drop and create order.
6. Test the migration against the rehearsal database.
7. Confirm verification passes.

Do not run an unreviewed migration against a production database.

---

# Example Thursday procedure

For a migration affecting `loans_sch_info`:

```powershell
# 1. Configure the target database in .env

# 2. Discover
python -m migrator discover loans_sch_info

# 3. Plan
python -m migrator plan-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql

# 4. Review the complete plan

# 5. Apply
python -m migrator apply-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql

# 6. Run tests after the rehearsal
python -m pytest -q
```

A successful application should report:

```text
FAMILY MIGRATION APPLIED SUCCESSFULLY
VERIFICATION PASSED
```

---

# Troubleshooting

## Managed object family is ambiguous

Example:

```text
ValueError: Managed object family is ambiguous
```

Check whether multiple matching objects exist in the database.

If the family has multiple published views, use a schema-qualified published-view SQL definition so the intended schema can be resolved.

---

## Duplicate column

Example:

```text
psycopg.errors.DuplicateColumn
```

Inspect the generated/input SQL and make sure each output column has a unique name.

For example, do not include both:

```sql
migration_test_marker,
'migration_test_marker' AS migration_test_marker
```

---

## Verification failed

Do not immediately retry.

First inspect:

* The migration SQL
* The generated plan
* The object definitions
* The reported verification error
* Whether the target database is the intended database

The migration transaction is rolled back when execution itself fails.

---

# Project status

The migration engine has been tested against a staging-schema rehearsal covering:

* Family discovery
* Cross-schema objects
* Dependency discovery
* Dependency ordering
* Existing versus proposed definitions
* Actual PostgreSQL object types
* Index preservation
* Downstream object recreation
* Schema changes
* Post-migration verification

The full automated test suite and staging integration tests should be run before operational use.
