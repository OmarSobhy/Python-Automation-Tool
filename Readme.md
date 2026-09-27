# pg-view-migrator

A PostgreSQL migration tool for safely changing SQL-backed database objects while preserving their existing PostgreSQL types, dependencies, indexes, ownership, comments, and other catalog metadata.

The tool is designed around **dependency-aware migrations** and supports migrating a managed family of related objects.

## What it does

`pg-view-migrator` can:

- discover managed object families;
- resolve migration SQL files to existing database objects;
- preserve the actual PostgreSQL object type;
- detect and follow PostgreSQL view/materialized-view dependencies;
- drop dependent objects before their referenced objects;
- recreate objects in dependency order;
- apply new SQL only to explicitly migrated family objects;
- recreate downstream objects from their existing database definitions;
- preserve indexes, ownership, comments, and materialized-view populated/unpopulated state;
- recreate tables with `WITH NO DATA`;
- verify the resulting objects and captured dependency relationships.

The operational flow is:

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

## Supported object types

The migration engine supports:

- `TABLE`
- `VIEW`
- `MATERIALIZED VIEW`

The **existing PostgreSQL catalog type is authoritative**. Filename prefixes such as `v_`, `t_`, and `mv_` are naming conventions only.

For example, `mv_loans_sch_info.sql` does not force `public.mv_loans_sch_info` to become a materialized view. If the existing object is a PostgreSQL `VIEW`, it remains a `VIEW` after migration.

## Managed object families

A family groups related objects by logical name. For example:

```text
loans_sch_info
```

may resolve to:

```text
auto_views.t_loans_sch_info
auto_views.v_loans_sch_info
public.mv_loans_sch_info
```

The family is resolved from the database catalog. If matching objects make the family ambiguous, the migration stops rather than guessing.

A schema-qualified SQL definition can be used when the published-view schema must be identified explicitly.

## Migration SQL and dependency behavior

Migration SQL defines the objects you intentionally want to change.

Objects explicitly supplied to `plan-family` / `apply-family` receive the supplied definition and appear in the plan as **`NEW SQL`**.

Objects discovered only because they depend on the migrated family are normally recreated from their **existing database definitions** and appear as **`EXISTING SQL`**.

This means changing one family does not silently replace the SQL definitions of downstream objects.

Objects are dropped in dependency-safe order:

```text
dependent → referenced
```

and recreated in the opposite direction:

```text
referenced → dependent
```

## Schema changes

Schema changes are supported through the supplied family SQL. For example, adding a new output column to a family definition causes the affected family objects to be recreated using the new definition.

Downstream objects retain their existing SQL definitions unless they are explicitly part of the migration family.

Post-migration verification checks the resulting objects and the captured dependency relationships.

## Metadata preservation

Relevant existing metadata is captured/restored, including:

- ownership;
- comments;
- index definitions;
- materialized-view populated/unpopulated state.

Indexes are restored after object recreation.

## Tables and data

Tables are recreated with:

```sql
WITH NO DATA
```

Therefore the tool changes/recreates table structure but **does not copy table rows**. Any data backfill or population must be handled separately.

## Installation

Install the Python dependencies:

```powershell
pip install -r requirements.txt
```

The project expects PostgreSQL connection settings in `.env`.

Example:

```env
PGHOST=localhost
PGPORT=5433
PGDATABASE=mylo_local
PGUSER=postgres
PGPASSWORD=YOUR_POSTGRES_PASSWORD
```

Always confirm `PGDATABASE` before applying a migration.

## Discover a family

```powershell
python -m migrator discover loans_sch_info
```

Use discovery before planning so you can confirm the objects and schemas resolved from the target database.

## Plan a family migration

For example:

```powershell
python -m migrator plan-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

Review the complete plan. It shows the proposed objects, actual object types, `NEW SQL` / `EXISTING SQL` classification, dependency ordering, index restoration, and metadata restoration.

Do not apply an unexpected plan.

## Apply a family migration

After reviewing the plan:

```powershell
python -m migrator apply-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

A successful migration should end with:

```text
FAMILY MIGRATION APPLIED SUCCESSFULLY
VERIFICATION PASSED
```

If execution fails, the migration transaction is rolled back. A successful SQL execution without `VERIFICATION PASSED` should not be treated as a completed migration.

## Rehearsal workflow

For a real migration:

1. Prepare the SQL definitions for the objects intentionally being changed.
2. Point `.env` at the rehearsal/schema-clone database.
3. Run the automated tests:

   ```powershell
   python -m pytest -q
   ```

4. Discover the family.
5. Generate and review the migration plan.
6. Apply the migration to the rehearsal database.
7. Confirm `VERIFICATION PASSED`.
8. Run relevant application-level checks.
9. Point `.env` at the real target.
10. Discover the family again on the target.
11. Generate a fresh target plan and review it.
12. Apply only after the target plan is confirmed.
13. Run post-migration tests and application checks.

## Testing

Run the complete suite:

```powershell
python -m pytest -q
```

Run the staging integration test when the staging/rehearsal database is configured:

```powershell
python -m pytest tests/test_staging_family_migration.py -q
```

The repository's current documentation expects the staging integration environment to use `PGDATABASE=mylo_local`.

## Rehearsal database

The project can be exercised against a schema-only clone of the staging database. This is useful for testing real PostgreSQL schemas, object types, cross-schema dependencies, indexes, downstream views, and dependency ordering without requiring production data.

## Materialized views

For an existing materialized view, the migrator preserves whether it was populated or unpopulated.

If it was unpopulated, recreation uses `WITH NO DATA`.

Again, the filename does not determine whether an object is a materialized view.

## Dependency limitations

The dependency graph comes from PostgreSQL catalog information. External applications, jobs, ETL processes, and workflows can have relationships that PostgreSQL does not record.

Those external dependencies must be validated separately during the migration.

## Safety checklist

Before applying:

1. Confirm the target database.
2. Discover the family.
3. Review the complete plan.
4. Confirm actual PostgreSQL object types.
5. Confirm intended objects are `NEW SQL`.
6. Confirm unchanged downstream objects are `EXISTING SQL`.
7. Review drop/create order.
8. Rehearse against a safe database.
9. Confirm `VERIFICATION PASSED`.
10. Perform application-level validation.

Stop rather than guessing if the family is ambiguous, the target database is wrong, the plan is unexpected, object types are unexpected, or verification fails.

## Troubleshooting

### Managed object family is ambiguous

Inspect the discovered objects and schemas. Do not guess. If multiple published views exist, use a schema-qualified published-view SQL definition to identify the intended object.

### Duplicate column

For `psycopg.errors.DuplicateColumn`, inspect the proposed SQL and ensure every output column has a unique name.

### Verification failed

Do not immediately retry. Inspect the migration SQL, generated plan, resulting object definitions, verification error, and target database configuration.

## Repository layout

```text
.
├── migrator/
├── schema/
├── tests/
├── MIGRATION_RUNBOOK.md
├── Readme.md
├── migration_preview.txt
├── rehearse_loans.py
├── staging_structure.sql
├── t_collection_base_changed.sql
├── t_merchant_branch_changed.sql
├── .env.example
└── docker-compose.yml
```

## Scope

`pg-view-migrator` is a PostgreSQL schema/object migration tool. It is not a general-purpose table-data migration or backfill system.
