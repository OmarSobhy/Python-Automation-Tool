
from .db import Database
from .models import (
    Column,
    DatabaseObject,
    EffectivePermission,
    Index,
    ObjectGrant,
)


OBJECT_SQL = """
SELECT
    c.oid,
    n.nspname AS schema_name,
    c.relname AS object_name,
    CASE c.relkind
        WHEN 'r' THEN 'TABLE'
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW'
    END AS object_type,
    CASE
        WHEN c.relkind IN ('v', 'm')
        THEN pg_get_viewdef(c.oid, true)
        ELSE NULL
    END AS definition,
    pg_get_userbyid(c.relowner) AS owner,
    obj_description(c.oid, 'pg_class') AS comment,
    CASE
        WHEN c.relkind = 'm' THEN c.relispopulated
        ELSE NULL
    END AS materialized_view_populated
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.oid = %s
  AND c.relkind IN ('r', 'v', 'm');
"""


COLUMN_SQL = """
SELECT
    a.attnum AS column_number,
    a.attname AS column_name,
    pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
    NOT a.attnotnull AS nullable
FROM pg_attribute a
WHERE a.attrelid = %s
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum;
"""


INDEX_SQL = """
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = %s
  AND tablename = %s
ORDER BY indexname;
"""


GRANTS_SQL = """
SELECT
    CASE
        WHEN acl.grantee = 0 THEN 'PUBLIC'
        ELSE pg_get_userbyid(acl.grantee)
    END AS grantee,
    acl.privilege_type,
    acl.is_grantable
FROM pg_class c
CROSS JOIN LATERAL aclexplode(c.relacl) AS acl
WHERE c.oid = %s
  AND c.relacl IS NOT NULL
  AND acl.grantee <> c.relowner
ORDER BY grantee, privilege_type;
"""


EFFECTIVE_PERMISSIONS_SQL = """
SELECT
    r.rolname AS role_name,
    p.privilege_type,
    has_table_privilege(
        r.oid,
        c.oid,
        p.privilege_type
    ) AS can_access
FROM pg_class c
CROSS JOIN pg_roles r
CROSS JOIN (
    VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
           ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
) AS p(privilege_type)
WHERE c.oid = %s
  AND r.rolcanlogin

UNION ALL

SELECT
    'PUBLIC' AS role_name,
    p.privilege_type,
    has_table_privilege(
        'public',
        c.oid,
        p.privilege_type
    ) AS can_access
FROM pg_class c
CROSS JOIN (
    VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
           ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')
) AS p(privilege_type)
WHERE c.oid = %s

ORDER BY role_name, privilege_type;
"""


def get_object(db: Database, oid: int) -> DatabaseObject:
    rows = db.query(OBJECT_SQL, (oid,))

    if not rows:
        raise ValueError(f"Object with OID {oid} was not found")

    (
        object_oid,
        schema_name,
        object_name,
        object_type,
        definition,
        owner,
        comment,
        materialized_view_populated,
    ) = rows[0]

    columns = [
        Column(
            name=name,
            data_type=data_type,
            nullable=nullable,
            position=position,
        )
        for position, name, data_type, nullable
        in db.query(COLUMN_SQL, (oid,))
    ]

    indexes = [
        Index(name=index_name, definition=index_definition)
        for index_name, index_definition
        in db.query(INDEX_SQL, (schema_name, object_name))
    ]

    grants = [
        ObjectGrant(
            grantee=grantee,
            privilege=privilege,
            grantable=grantable,
        )
        for grantee, privilege, grantable
        in db.query(GRANTS_SQL, (oid,))
    ]

    effective_permissions = [
        EffectivePermission(
            role=role_name,
            privilege=privilege,
            can_access=can_access,
        )
        for role_name, privilege, can_access
        in db.query(EFFECTIVE_PERMISSIONS_SQL, (oid, oid))
    ]

    return DatabaseObject(
        oid=object_oid,
        schema=schema_name,
        name=object_name,
        object_type=object_type,
        definition=definition,
        owner=owner,
        comment=comment,
        materialized_view_populated=materialized_view_populated,
        columns=columns,
        indexes=indexes,
        grants=grants,
        effective_permissions=effective_permissions,
    )
