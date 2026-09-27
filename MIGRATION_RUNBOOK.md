# PostgreSQL Migration Runbook

## Purpose

Operational procedure for running a `pg-view-migrator` family migration safely.

The migration should be rehearsed against the staging/schema clone before the real target. The tool changes database objects; it does **not** migrate table rows.

---

# 1. Pre-migration checks

## 1.1 Confirm the project

From the repository root:

```powershell
Get-ChildItem
```

Confirm the migration engine, tests, schema fixtures, and runbook are present.

For the `loans_sch_info` example, confirm the intended SQL files are present:

```text
t_loans_sch_info.sql
v_loans_sch_info.sql
mv_loans_sch_info.sql
```

## 1.2 Confirm the target database

```powershell
Get-Content .env
```

Verify:

```text
PGHOST
PGPORT
PGDATABASE
PGUSER
```

are pointing to the intended database.

**STOP if the target is wrong.**

---

# 2. Rehearsal

## 2.1 Run the automated tests

```powershell
python -m pytest -q
```

Investigate unexpected failures before continuing.

## 2.2 Run the staging integration test

When the staging/rehearsal database is configured as expected:

```powershell
$env:PGDATABASE="mylo_local"
python -m pytest tests/test_staging_family_migration.py -q
```

Do not confuse a skipped integration test with a passing migration rehearsal.

## 2.3 Discover the family

```powershell
python -m migrator discover loans_sch_info
```

For the current rehearsal example, confirm the expected family:

```text
auto_views.t_loans_sch_info
auto_views.v_loans_sch_info
public.mv_loans_sch_info
```

The database catalog, not the filename, determines the PostgreSQL object type.

---

# 3. Generate and review the migration plan

Run:

```powershell
python -m migrator plan-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

Review the **entire** plan before applying it.

## 3.1 Confirm family objects

Objects explicitly supplied as migration inputs should be classified as:

```text
NEW SQL
```

For the example family, confirm the actual catalog types shown by the plan rather than assuming them from the prefixes.

## 3.2 Confirm downstream objects

Objects discovered only because they depend on the family should normally be:

```text
EXISTING SQL
```

unless they were intentionally included in the migration inputs.

## 3.3 Confirm ordering

Verify that:

```text
drop:   dependent → referenced
create: referenced → dependent
```

Also review index restoration and metadata restoration.

## 3.4 Stop conditions

Do not apply if:

- the family is ambiguous;
- the target database is wrong;
- an unexpected object appears;
- an actual PostgreSQL object type is unexpected;
- a family object is not receiving the intended `NEW SQL`;
- an unchanged downstream object unexpectedly receives `NEW SQL`;
- drop/create ordering is unexpected.

---

# 4. Apply the rehearsal migration

After the plan has been reviewed:

```powershell
python -m migrator apply-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

A successful run should end with:

```text
FAMILY MIGRATION APPLIED SUCCESSFULLY
VERIFICATION PASSED
```

If either expected result is missing, stop and investigate.

If execution itself fails, the migration transaction is rolled back.

---

# 5. Validate the rehearsal

Confirm that:

- intended family objects exist;
- their actual PostgreSQL types are correct;
- the new definitions are present on explicitly migrated objects;
- downstream objects retain their existing definitions unless intentionally migrated;
- indexes and relevant metadata were restored;
- captured dependency relationships are still valid;
- expected schema changes are present.

For example, to inspect a new output column:

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

### Important: table data

Tables are recreated with:

```sql
WITH NO DATA
```

The migrator does not copy existing table rows. Any required data backfill must be handled separately.

### Materialized views

An existing materialized view's populated/unpopulated state is preserved. An unpopulated materialized view is recreated with `WITH NO DATA`.

---

# 6. Application-level rehearsal validation

The migrator validates database structure and dependency relationships. It cannot see every dependency outside PostgreSQL.

Check relevant:

- application queries;
- reports/screens;
- ETL jobs;
- scheduled workflows;
- external consumers;
- data-population processes.

---

# 7. Prepare the real target

Point `.env` at the real target database and verify it again:

```powershell
Get-Content .env
```

**Double-check `PGDATABASE`, host, port, and user.**

Do not assume the target catalog is identical to the rehearsal catalog.

---

# 8. Discover and plan again on the target

Run discovery again:

```powershell
python -m migrator discover loans_sch_info
```

Then generate a **fresh** target plan:

```powershell
python -m migrator plan-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

Review the target plan from scratch.

Confirm:

- family objects;
- schemas;
- actual PostgreSQL object types;
- `NEW SQL` / `EXISTING SQL` classification;
- dependency drop order;
- dependency create order;
- indexes;
- metadata.

Do not reuse the rehearsal plan as a substitute for a target plan.

---

# 9. Apply the target migration

Only after the target plan is approved:

```powershell
python -m migrator apply-family loans_sch_info t_loans_sch_info.sql v_loans_sch_info.sql mv_loans_sch_info.sql
```

Wait for:

```text
FAMILY MIGRATION APPLIED SUCCESSFULLY
VERIFICATION PASSED
```

If the result is unexpected, stop.

---

# 10. Post-migration checks

Run the automated suite:

```powershell
python -m pytest -q
```

Then perform application-level checks for the affected objects.

Confirm the intended schema change and downstream behavior before declaring the migration complete.

---

# Troubleshooting

## Duplicate column

For:

```text
psycopg.errors.DuplicateColumn
```

inspect the proposed SQL and ensure output column names are unique.

For example, do not define the same output name twice:

```sql
migration_test_marker,
'migration_test_marker' AS migration_test_marker
```

## Ambiguous family

For:

```text
Managed object family is ambiguous
```

stop and inspect the discovered schemas/objects. Do not guess.

If multiple published views exist, use a schema-qualified published-view SQL definition to identify the intended schema.

## Verification failure

Stop. Do not immediately retry.

Inspect:

- migration SQL;
- generated plan;
- resulting object definitions;
- reported verification error;
- target database configuration.

Execution errors are rolled back by the migration transaction, but verification failure still requires investigation.

---

# Success criteria

A migration is complete only when:

- the correct target database was confirmed;
- the target family was discovered correctly;
- the target plan was reviewed;
- intended family objects received `NEW SQL`;
- unchanged downstream objects retained `EXISTING SQL`;
- object types are correct;
- drop/create ordering is correct;
- execution completed without SQL errors;
- `FAMILY MIGRATION APPLIED SUCCESSFULLY` was reported;
- `VERIFICATION PASSED` was reported;
- automated tests passed as applicable;
- application-level validation passed.

# Emergency principle

If the target is wrong, the family is ambiguous, the plan is unexpected, the SQL is unexpected, or verification fails:

**STOP — investigate before continuing.**
