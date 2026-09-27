from migrator.db import Database


db = Database()

rows = db.query(
    """
    SELECT
        n.nspname,
        c.relname,
        c.relkind
    FROM pg_class c
    JOIN pg_namespace n
        ON n.oid = c.relnamespace
    WHERE n.nspname = 'family_migration_test'
    ORDER BY c.relname;
    """
)

for row in rows:
    print(row)

db.close()