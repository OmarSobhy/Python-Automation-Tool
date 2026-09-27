import re
import uuid

from .db import Database
from .models import Column, ProposedObject
from .sql_utils import qualified_name


COLUMN_SQL = """
SELECT
    a.attnum AS column_number,
    a.attname AS column_name,
    pg_catalog.format_type(
        a.atttypid,
        a.atttypmod
    ) AS data_type,
    NOT a.attnotnull AS nullable
FROM pg_attribute a
WHERE a.attrelid = %s::regclass
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY a.attnum;
"""


def parse_proposed_object(sql: str) -> ProposedObject:
    text = sql.strip()

    match = re.search(
        r"""
        \bCREATE\s+
        (?:OR\s+REPLACE\s+)?
        (?P<object_type>
            TABLE
            |
            VIEW
            |
            MATERIALIZED\s+VIEW
        )
        \s+
        (?P<object_name>
            (?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)
            \.
            (?:"[^"]+"|[A-Za-z_][A-Za-z0-9_]*)
        )
        """,
        text,
        re.IGNORECASE | re.VERBOSE,
    )

    if not match:
        return ProposedObject(
            schema="",
            name="",
            sql=text.rstrip(";").strip(),
        )

    object_name = match.group("object_name")
    object_type = match.group("object_type").upper()

    schema, name = object_name.split(".", 1)

    remainder = text[match.end():].strip()

    if object_type in {
        "VIEW",
        "MATERIALIZED VIEW",
    }:
        as_match = re.match(
            r"""
            AS\s+
            (?P<body>.*?)
            \s*
            (?:WITH\s+NO\s+DATA\s*)?
            ;?\s*$
            """,
            remainder,
            re.IGNORECASE | re.DOTALL | re.VERBOSE,
        )

        if as_match:
            body = as_match.group("body").strip()
        else:
            body = remainder.rstrip(";").strip()

    else:
        body = remainder.rstrip(";").strip()

    return ProposedObject(
        schema=schema.strip('"'),
        name=name.strip('"'),
        sql=body,
    )


def parse_proposed_objects(
    sql_files: list[str],
) -> list[ProposedObject]:
    return [
        parse_proposed_object(sql)
        for sql in sql_files
    ]


def proposed_object_map(
    proposed_objects: list[ProposedObject],
) -> dict[str, ProposedObject]:
    result = {}

    for proposed in proposed_objects:
        if not proposed.name:
            raise ValueError(
                "Migration SQL must contain a named object"
            )

        if proposed.name in result:
            raise ValueError(
                f"Duplicate migration object: {proposed.name}"
            )

        result[proposed.name] = proposed

    return result


def get_proposed_columns(
    db: Database,
    sql: str,
    object_type: str | None = None,
) -> list[Column]:
    candidate_name = (
        f"__migration_candidate_{uuid.uuid4().hex}"
    )

    candidate = qualified_name(
        "pg_temp",
        candidate_name,
    )

    parsed = parse_proposed_object(sql)

    if object_type is None:
        match = re.search(
            r"""
            \bCREATE\s+
            (?:OR\s+REPLACE\s+)?
            (?P<object_type>
                TABLE
                |
                VIEW
                |
                MATERIALIZED\s+VIEW
            )
            \b
            """,
            sql,
            re.IGNORECASE | re.VERBOSE,
        )

        if match:
            object_type = (
                match.group("object_type").upper()
            )

    body = (
        parsed.sql
        if parsed.name
        else sql.strip()
    )

    if object_type == "MATERIALIZED VIEW":
        object_type = "VIEW"

    try:
        if object_type == "TABLE":
            body = body.strip()

            if re.match(
                r"^AS\b",
                body,
                re.IGNORECASE,
            ):
                body = re.sub(
                    r"^AS\b",
                    "",
                    body,
                    count=1,
                    flags=re.IGNORECASE,
                ).strip()

                db.query(
                    f"""
                    CREATE TEMP TABLE {candidate_name}
                    AS
                    {body}
                    WITH NO DATA
                    """
                )

            elif body.startswith("("):
                db.query(
                    f"""
                    CREATE TEMP TABLE {candidate_name}
                    {body}
                    """
                )

            else:
                db.query(
                    f"""
                    CREATE TEMP TABLE {candidate_name}
                    AS
                    {body}
                    WITH NO DATA
                    """
                )

        else:
            db.query(
                f"""
                CREATE TEMP VIEW {candidate_name} AS
                {body}
                """
            )

        rows = db.query(
            COLUMN_SQL,
            (candidate,),
        )

        return [
            Column(
                name=name,
                data_type=data_type,
                nullable=nullable,
                position=position,
            )
            for (
                position,
                name,
                data_type,
                nullable,
            ) in rows
        ]

    except Exception:
        db.connection.rollback()
        raise

    finally:
        try:
            db.query(
                f"""
                DROP TABLE IF EXISTS {candidate_name};
                """
            )
        except Exception:
            db.connection.rollback()

        try:
            db.query(
                f"""
                DROP VIEW IF EXISTS {candidate_name};
                """
            )
        except Exception:
            db.connection.rollback()