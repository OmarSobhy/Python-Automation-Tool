from dataclasses import dataclass
from enum import Enum

from .db import Database
from .models import ProposedObject


class FamilyStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class ManagedObjectFamily:
    name: str

    source_view_oid: int | None
    source_view_schema: str | None
    source_view_name: str | None

    live_table_oid: int | None
    live_table_schema: str | None
    live_table_name: str | None

    published_view_oid: int | None
    published_view_schema: str | None
    published_view_name: str | None

    source_view_count: int = 0
    live_table_count: int = 0
    published_view_count: int = 0

    @property
    def status(self) -> FamilyStatus:
        if (
            self.source_view_count > 1
            or self.live_table_count > 1
        ):
            return FamilyStatus.AMBIGUOUS

        if self.published_view_count > 1:
            # Multiple published views are only ambiguous when
            # there is no canonical public.mv_<family> object.
            if self.published_view_schema != "public":
                return FamilyStatus.AMBIGUOUS

        if not self.is_complete:
            return FamilyStatus.INCOMPLETE

        return FamilyStatus.COMPLETE

    @property
    def is_complete(self) -> bool:
        return (
            self.source_view_oid is not None
            and self.live_table_oid is not None
            and self.published_view_oid is not None
        )


DISCOVER_FAMILIES_SQL = """
WITH source_views AS (
    SELECT
        c.oid,
        n.nspname AS schema_name,
        c.relname AS object_name,
        substring(c.relname FROM 3) AS family_name
    FROM pg_class c
    JOIN pg_namespace n
        ON n.oid = c.relnamespace
    WHERE c.relkind = 'v'
      AND c.relname LIKE 'v\\_%' ESCAPE '\\'
      AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\'
      AND n.nspname <> 'information_schema'
),
live_tables AS (
    SELECT
        c.oid,
        n.nspname AS schema_name,
        c.relname AS object_name,
        substring(c.relname FROM 3) AS family_name
    FROM pg_class c
    JOIN pg_namespace n
        ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p')
      AND c.relname LIKE 't\\_%' ESCAPE '\\'
),
published_views AS (
    SELECT
        c.oid,
        n.nspname AS schema_name,
        c.relname AS object_name,
        substring(c.relname FROM 4) AS family_name
    FROM pg_class c
    JOIN pg_namespace n
        ON n.oid = c.relnamespace
    WHERE c.relkind IN ('v', 'm')
      AND c.relname LIKE 'mv\\_%' ESCAPE '\\'
),
family_names AS (
    SELECT family_name FROM source_views
    UNION
    SELECT family_name FROM live_tables
    UNION
    SELECT family_name FROM published_views
),
published_counts AS (
    SELECT
        family_name,
        count(*) AS published_view_count
    FROM published_views
    GROUP BY family_name
)
SELECT
    names.family_name,

    s.oid AS source_view_oid,
    s.schema_name AS source_view_schema,
    s.object_name AS source_view_name,

    t.oid AS live_table_oid,
    t.schema_name AS live_table_schema,
    t.object_name AS live_table_name,

    p.oid AS published_view_oid,
    p.schema_name AS published_view_schema,
    p.object_name AS published_view_name,

    (
        SELECT count(*)
        FROM source_views s2
        WHERE s2.family_name = names.family_name
    ) AS source_view_count,

    (
        SELECT count(*)
        FROM live_tables t2
        WHERE t2.family_name = names.family_name
    ) AS live_table_count,

    COALESCE(
        pc.published_view_count,
        0
    ) AS published_view_count

FROM family_names names

LEFT JOIN source_views s
    ON s.family_name = names.family_name
   AND (
        SELECT count(*)
        FROM source_views s2
        WHERE s2.family_name = names.family_name
   ) = 1

LEFT JOIN live_tables t
    ON t.family_name = names.family_name
   AND (
        SELECT count(*)
        FROM live_tables t2
        WHERE t2.family_name = names.family_name
   ) = 1

LEFT JOIN published_views p
    ON p.family_name = names.family_name
   AND (
        p.schema_name = 'public'
        OR (
            p.schema_name <> 'public'
            AND NOT EXISTS (
                SELECT 1
                FROM published_views public_p
                WHERE public_p.family_name = names.family_name
                  AND public_p.schema_name = 'public'
            )
            AND (
                SELECT count(*)
                FROM published_views p2
                WHERE p2.family_name = names.family_name
            ) = 1
        )
   )

LEFT JOIN published_counts pc
    ON pc.family_name = names.family_name

ORDER BY names.family_name;
"""


def discover_families(
    db: Database,
) -> list[ManagedObjectFamily]:
    rows = db.query(
        DISCOVER_FAMILIES_SQL
    )

    return [
        ManagedObjectFamily(
            name=row[0],

            source_view_oid=row[1],
            source_view_schema=row[2],
            source_view_name=row[3],

            live_table_oid=row[4],
            live_table_schema=row[5],
            live_table_name=row[6],

            published_view_oid=row[7],
            published_view_schema=row[8],
            published_view_name=row[9],

            source_view_count=row[10],
            live_table_count=row[11],
            published_view_count=row[12],
        )
        for row in rows
    ]


def get_managed_family(
    db: Database,
    family_name: str,
    published_schema: str | None = None,
) -> ManagedObjectFamily:
    families = discover_families(db)

    matches = [
        family
        for family in families
        if family.name == family_name
    ]

    if not matches:
        raise ValueError(
            f"Managed object family not found: {family_name}"
        )

    if published_schema:
        schema_matches = [
            family
            for family in matches
            if family.published_view_schema
            == published_schema
        ]

        if len(schema_matches) == 1:
            return schema_matches[0]

        if len(schema_matches) > 1:
            raise ValueError(
                "Managed object family is ambiguous: "
                f"{family_name} "
                f"(multiple published views in schema "
                f"{published_schema})"
            )

        # The proposed SQL schema may be a placeholder rather
        # than the actual schema of the managed family. In that
        # case, fall back to the uniquely discovered family.
        #
        # If there is a canonical public published view, prefer
        # that family object.
        public_matches = [
            family
            for family in matches
            if family.published_view_schema == "public"
        ]

        if len(public_matches) == 1:
            return public_matches[0]

        if len(matches) == 1:
            family = matches[0]

            if family.status == FamilyStatus.AMBIGUOUS:
                raise ValueError(
                    f"Managed object family is ambiguous: "
                    f"{family_name}"
                )

            return family

        raise ValueError(
            "Managed object family not found in published schema: "
            f"{family_name} ({published_schema})"
        )

    if len(matches) == 1:
        family = matches[0]

        if family.status == FamilyStatus.AMBIGUOUS:
            raise ValueError(
                f"Managed object family is ambiguous: "
                f"{family_name}"
            )

        return family

    raise ValueError(
        f"Managed object family is ambiguous: {family_name}"
    )



def get_family_objects(
    family: ManagedObjectFamily,
) -> list[tuple[int, str, str, str]]:
    objects = []

    if family.source_view_oid is not None:
        objects.append(
            (
                family.source_view_oid,
                family.source_view_schema,
                family.source_view_name,
                "SOURCE VIEW",
            )
        )

    if family.live_table_oid is not None:
        objects.append(
            (
                family.live_table_oid,
                family.live_table_schema,
                family.live_table_name,
                "LIVE TABLE",
            )
        )

    if family.published_view_oid is not None:
        objects.append(
            (
                family.published_view_oid,
                family.published_view_schema,
                family.published_view_name,
                "PUBLISHED VIEW",
            )
        )

    return objects


def match_proposed_to_family(
    proposed_objects,
    family: ManagedObjectFamily,
):
    family_objects = get_family_objects(family)

    matches = []

    for proposed in proposed_objects:
        candidates = [
            item
            for item in family_objects
            if item[2] == proposed.name
        ]

        if not candidates:
            raise ValueError(
                "Object from migration SQL not found in family "
                f"{family.name}: {proposed.name}"
            )

        if len(candidates) > 1:
            raise ValueError(
                "Object name is ambiguous in family "
                f"{family.name}: {proposed.name}"
            )

        matches.append(
            (
                proposed,
                candidates[0],
            )
        )

    return matches


def resolve_proposed_objects(
    db: Database,
    family_name: str,
    proposed_objects: list[ProposedObject],
) -> list[tuple[ProposedObject, int]]:
    published_schema = None

    for proposed in proposed_objects:
        if proposed.name == f"mv_{family_name}":
            if proposed.schema:
                published_schema = proposed.schema
            break

    family = get_managed_family(
        db,
        family_name,
        published_schema=published_schema,
    )

    resolved = []

    for proposed in proposed_objects:
        if proposed.name == f"v_{family_name}":
            oid = family.source_view_oid

        elif proposed.name == f"t_{family_name}":
            oid = family.live_table_oid

        elif proposed.name == f"mv_{family_name}":
            oid = family.published_view_oid

        else:
            raise ValueError(
                "Migration object does not belong to managed family "
                f"{family_name}: {proposed.name}"
            )

        if oid is None:
            raise ValueError(
                f"Managed object family is incomplete: "
                f"{family_name} ({proposed.name} is missing)"
            )

        resolved.append(
            (
                proposed,
                oid,
            )
        )

    return resolved


def family_name_from_object_name(
    name: str,
) -> str:
    if name.startswith("mv_"):
        return name[3:]

    if (
        name.startswith("t_")
        or name.startswith("v_")
    ):
        return name[2:]

    raise ValueError(
        f"Cannot determine family name from object: {name}"
    )
