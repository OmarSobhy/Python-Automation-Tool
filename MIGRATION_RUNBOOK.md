# PostgreSQL Migration Runbook

## Purpose

Operational checklist for running a `pg-view-migrator` family migration.

The migration should always be rehearsed against the staging/schema clone before running against the target database.

---

# Before the migration

## 1. Confirm the project

```powershell
cd C:\Users\OmarSobhy\Desktop\pg-view-migrator
```

Confirm the migration files are present:

```powershell
Get-ChildItem *.sql
```

For the `loans_sch_info` migration, expected files are:

```text
t_loans_sch_info.sql
v_loans_sch_info.sql
mv_loans_sch_info.sql
```

---

## 2. Confirm the target database

Check `.env`:

```powershell
Get-Content .env
```

Confirm:

```text
PGHOST
PGPORT
PGDATABASE
PGUSER
```

are pointing to the intended database.

**Stop if the database is not the intended target.**

---

# Rehearsal

## 3. Run the full tests

```powershell
python -m pytest -q
```

All tests must pass.

---

## 4. Run staging integration tests

Set:

```text
PGDATABASE=mylo_local
```

Then:

```powershell
python -m pytest tests/test_staging_family_migration.py -q
```

All staging tests must pass.

---

## 5. Discover the family

```powershell
python -m migrator discover loans_sch_info
```

Confirm the expected family objects are present.

For the current rehearsal:

```text
auto_views.t_loans_sch_info
auto_views.v_loans_sch_info
public.mv_loans_sch_info
```

---

## 6. Generate the migration plan

```powershell
python -m migrator plan-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

Review the entire output.

Confirm:

### Family objects

These should be:

```text
auto_views.t_loans_sch_info [TABLE] -> NEW SQL
auto_views.v_loans_sch_info [VIEW] -> NEW SQL
public.mv_loans_sch_info [VIEW] -> NEW SQL
```

### Downstream objects

These should normally be:

```text
-> EXISTING SQL
```

unless intentionally included in the family.

### Object types

Confirm the displayed PostgreSQL types are correct.

Do not infer the type from the filename.

---

# Rehearsal application

## 7. Apply the migration to the rehearsal database

```powershell
python -m migrator apply-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

Expected successful result:

```text
FAMILY MIGRATION APPLIED SUCCESSFULLY
VERIFICATION PASSED
```

If this does not appear, stop and investigate.

---

## 8. Confirm the schema change

For a migration that adds a column such as:

```text
migration_schema_test
```

verify that the expected family objects contain the column.

The exact query can be run through PostgreSQL/pgAdmin:

```sql
SELECT
    table_schema,
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE (table_schema, table_name) IN (
    ('auto_views', 't_loans_sch_info'),
    ('auto_views', 'v_loans_sch_info'),
    ('public', 'mv_loans_sch_info')
)
AND column_name = 'migration_schema_test'
ORDER BY table_schema, table_name;
```

The three family objects should be represented.

---

# Production / target migration

## 9. Point `.env` at the actual target

Change only the database configuration required for the target environment.

Verify it again:

```powershell
Get-Content .env
```

**Double-check the database name before continuing.**

---

## 10. Run discovery again

```powershell
python -m migrator discover loans_sch_info
```

Do not assume the rehearsal catalog is identical to the target.

Confirm the family objects and schemas.

---

## 11. Generate a fresh target plan

```powershell
python -m migrator plan-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

Review the target plan.

Do not reuse assumptions from the rehearsal.

Check:

* Family objects
* Schemas
* Actual object types
* `NEW SQL`
* `EXISTING SQL`
* Drop order
* Create order
* Indexes
* Metadata

---

## 12. Apply the target migration

Only after the target plan has been reviewed:

```powershell
python -m migrator apply-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

Wait for:

```text
FAMILY MIGRATION APPLIED SUCCESSFULLY
VERIFICATION PASSED
```

---

# If something fails

## SQL error

Stop.

Do not repeatedly retry without understanding the error.

Check the SQL files first.

Common example:

```text
DuplicateColumn
```

means the proposed query contains duplicate output column names.

---

## Ambiguous family

Example:

```text
Managed object family is ambiguous
```

Stop.

Inspect the discovered objects and schemas.

Do not guess which object should be migrated.

---

## Verification failure

Stop.

Inspect the verification error before taking further action.

Do not assume the migration is correct merely because SQL execution succeeded.

---

# Post-migration

## 13. Run tests

After the rehearsal:

```powershell
python -m pytest -q
```

For the final codebase, all tests should remain green.

---

## 14. Confirm application-level behavior

After the database migration, perform the relevant application checks for the affected objects.

The migration tool verifies database structure and dependencies; it does not replace application-level validation.

---

# Success criteria

The migration is considered successfully completed when:

* The correct target database was confirmed.
* The target family was discovered correctly.
* The target migration plan was reviewed.
* Expected family objects received `NEW SQL`.
* Unchanged downstream objects retained `EXISTING SQL`.
* Drop/create ordering was correct.
* The migration completed without SQL errors.
* `FAMILY MIGRATION APPLIED SUCCESSFULLY` was reported.
* `VERIFICATION PASSED` was reported.
* Required application-level checks passed.

---

# Emergency principle

If the plan is unexpected:

**STOP — do not apply.**

If the SQL is unexpected:

**STOP — do not apply.**

If verification fails:

**STOP — investigate before continuing.**
