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


SOURCE_VIEWS_SQL = """
SELECT
    c.oid,
    n.nspname AS schema_name,
    c.relname AS object_name,
    substring(c.relname FROM 3) AS family_name
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE c.relkind = 'v'
  AND n.nspname = 'auto_views'
  AND c.relname LIKE 'v\\_%' ESCAPE '\\'
ORDER BY c.relname;
"""


LIVE_TABLES_SQL = """
SELECT
    c.oid,
    n.nspname AS schema_name,
    c.relname AS object_name,
    substring(c.relname FROM 3) AS family_name
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname = 'auto_views'
  AND c.relname LIKE 't\\_%' ESCAPE '\\'
ORDER BY c.relname;
"""


CANONICAL_PUBLISHED_SQL = """
SELECT
    c.oid,
    n.nspname AS schema_name,
    c.relname AS object_name,
    substring(c.relname FROM 4) AS family_name
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE c.relkind IN ('v', 'm')
  AND n.nspname = 'public'
  AND c.relname LIKE 'mv\\_%' ESCAPE '\\'
ORDER BY c.relname;
"""


PUBLIC_NAMED_OBJECTS_SQL = """
SELECT
    c.oid,
    n.nspname AS schema_name,
    c.relname AS object_name
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE c.relkind IN ('v', 'm')
  AND n.nspname = 'public'
  AND c.relname NOT LIKE 'v\\_%' ESCAPE '\\'
  AND c.relname NOT LIKE 't\\_%' ESCAPE '\\'
  AND c.relname NOT LIKE 'mv\\_%' ESCAPE '\\'
ORDER BY c.relname;
"""


PUBLIC_NAMED_DEPENDENCIES_SQL = """
SELECT
    p.oid AS published_oid,
    p.relname AS published_name,
    d.refobjid AS referenced_oid
FROM pg_class p
JOIN pg_namespace pn
    ON pn.oid = p.relnamespace
JOIN pg_rewrite r
    ON r.ev_class = p.oid
JOIN pg_depend d
    ON d.objid = r.oid
WHERE p.relkind IN ('v', 'm')
  AND pn.nspname = 'public'
  AND p.relname NOT LIKE 'v\\_%' ESCAPE '\\'
  AND p.relname NOT LIKE 't\\_%' ESCAPE '\\'
  AND p.relname NOT LIKE 'mv\\_%' ESCAPE '\\';
"""


def discover_families(
    db: Database,
) -> list[ManagedObjectFamily]:
    source_rows = db.query(
        SOURCE_VIEWS_SQL
    )

    table_rows = db.query(
        LIVE_TABLES_SQL
    )

    canonical_rows = db.query(
        CANONICAL_PUBLISHED_SQL
    )

    public_named_rows = db.query(
        PUBLIC_NAMED_OBJECTS_SQL
    )

    dependency_rows = db.query(
        PUBLIC_NAMED_DEPENDENCIES_SQL
    )

    # ------------------------------------------------------------------
    # Build source-view groups.
    # ------------------------------------------------------------------

    source_by_family: dict[
        str,
        list[tuple[int, str, str]],
    ] = {}

    for (
        oid,
        schema_name,
        object_name,
        family_name,
    ) in source_rows:
        source_by_family.setdefault(
            family_name,
            [],
        ).append(
            (
                oid,
                schema_name,
                object_name,
            )
        )

    # ------------------------------------------------------------------
    # Build live-table groups.
    # ------------------------------------------------------------------

    table_by_family: dict[
        str,
        list[tuple[int, str, str]],
    ] = {}

    for (
        oid,
        schema_name,
        object_name,
        family_name,
    ) in table_rows:
        table_by_family.setdefault(
            family_name,
            [],
        ).append(
            (
                oid,
                schema_name,
                object_name,
            )
        )

    # ------------------------------------------------------------------
    # Build canonical published objects.
    #
    # There should normally be at most one public.mv_<family>
    # because PostgreSQL does not allow duplicate relation names
    # in the same schema.
    # ------------------------------------------------------------------

    canonical_by_family: dict[
        str,
        list[tuple[int, str, str]],
    ] = {}

    for (
        oid,
        schema_name,
        object_name,
        family_name,
    ) in canonical_rows:
        canonical_by_family.setdefault(
            family_name,
            [],
        ).append(
            (
                oid,
                schema_name,
                object_name,
            )
        )

    # ------------------------------------------------------------------
    # Build lookup for public.<family> fallback objects.
    # ------------------------------------------------------------------

    named_public_by_name: dict[
        str,
        tuple[int, str, str],
    ] = {}

    for (
        oid,
        schema_name,
        object_name,
    ) in public_named_rows:
        named_public_by_name[object_name] = (
            oid,
            schema_name,
            object_name,
        )

    # ------------------------------------------------------------------
    # Build dependency lookup:
    #
    # published OID -> referenced relation OIDs
    #
    # PostgreSQL stores view dependencies through pg_rewrite.
    # ------------------------------------------------------------------

    dependencies_by_published_oid: dict[
        int,
        set[int],
    ] = {}

    for (
        published_oid,
        _published_name,
        referenced_oid,
    ) in dependency_rows:
        dependencies_by_published_oid.setdefault(
            published_oid,
            set(),
        ).add(referenced_oid)

    # ------------------------------------------------------------------
    # A family must be discovered from:
    #
    #   auto_views.v_<family>
    #   auto_views.t_<family>
    #   public.mv_<family>
    #
    # public.<family> does NOT create a family by itself.
    # ------------------------------------------------------------------

    family_names = (
        set(source_by_family)
        | set(table_by_family)
        | set(canonical_by_family)
    )

    families: list[ManagedObjectFamily] = []

    for family_name in sorted(family_names):
        source_objects = source_by_family.get(
            family_name,
            [],
        )

        table_objects = table_by_family.get(
            family_name,
            [],
        )

        canonical_objects = canonical_by_family.get(
            family_name,
            [],
        )

        # --------------------------------------------------------------
        # Source view:
        #
        # Only use it when the family has exactly one source view.
        # --------------------------------------------------------------

        source_view_oid = None
        source_view_schema = None
        source_view_name = None

        if len(source_objects) == 1:
            (
                source_view_oid,
                source_view_schema,
                source_view_name,
            ) = source_objects[0]

        # --------------------------------------------------------------
        # Live table:
        #
        # Only use it when the family has exactly one live table.
        # --------------------------------------------------------------

        live_table_oid = None
        live_table_schema = None
        live_table_name = None

        if len(table_objects) == 1:
            (
                live_table_oid,
                live_table_schema,
                live_table_name,
            ) = table_objects[0]

        # --------------------------------------------------------------
        # Published object:
        #
        # 1. public.mv_<family> always wins.
        #
        # 2. Otherwise public.<family> is considered only when:
        #    - it exists
        #    - it is a view/materialized view
        #    - it depends on this family's source view OR live table
        # --------------------------------------------------------------

        published_view_oid = None
        published_view_schema = None
        published_view_name = None
        published_view_count = 0

        if canonical_objects:
            (
                published_view_oid,
                published_view_schema,
                published_view_name,
            ) = canonical_objects[0]

            # Canonical object takes precedence over any fallback.
            published_view_count = 1

        else:
            fallback = named_public_by_name.get(
                family_name
            )

            if fallback is not None:
                (
                    fallback_oid,
                    fallback_schema,
                    fallback_name,
                ) = fallback

                referenced_oids = (
                    dependencies_by_published_oid.get(
                        fallback_oid,
                        set(),
                    )
                )

                related_to_family = (
                    (
                        source_view_oid is not None
                        and source_view_oid in referenced_oids
                    )
                    or (
                        live_table_oid is not None
                        and live_table_oid in referenced_oids
                    )
                )

                if related_to_family:
                    published_view_oid = fallback_oid
                    published_view_schema = fallback_schema
                    published_view_name = fallback_name
                    published_view_count = 1

        families.append(
            ManagedObjectFamily(
                name=family_name,

                source_view_oid=source_view_oid,
                source_view_schema=source_view_schema,
                source_view_name=source_view_name,

                live_table_oid=live_table_oid,
                live_table_schema=live_table_schema,
                live_table_name=live_table_name,

                published_view_oid=published_view_oid,
                published_view_schema=published_view_schema,
                published_view_name=published_view_name,

                source_view_count=len(source_objects),
                live_table_count=len(table_objects),
                published_view_count=published_view_count,
            )
        )

    return families

GENERIC_FAMILY_LOOKUP_SQL = """
SELECT
    c.oid,
    n.nspname AS schema_name,
    c.relname AS object_name,
    c.relkind
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE c.relname IN (
    %s,
    %s,
    %s
)
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND n.nspname NOT IN (
      'pg_catalog',
      'information_schema'
  )
ORDER BY
    c.relname,
    n.nspname;
"""


def _discover_generic_family(
    db: Database,
    family_name: str,
    published_schema: str | None = None,
) -> ManagedObjectFamily | None:
    rows = db.query(
        GENERIC_FAMILY_LOOKUP_SQL,
        (
            f"v_{family_name}",
            f"t_{family_name}",
            f"mv_{family_name}",
        ),
    )

    by_name: dict[
        str,
        list[tuple[int, str, str, str]],
    ] = {}

    for (
        oid,
        schema_name,
        object_name,
        relkind,
    ) in rows:
        by_name.setdefault(
            object_name,
            [],
        ).append(
            (
                oid,
                schema_name,
                object_name,
                relkind,
            )
        )

    source_candidates = by_name.get(
        f"v_{family_name}",
        [],
    )

    table_candidates = by_name.get(
        f"t_{family_name}",
        [],
    )

    published_candidates = by_name.get(
        f"mv_{family_name}",
        [],
    )

    # A family member must resolve uniquely.
    if len(source_candidates) > 1:
        raise ValueError(
            "Managed object family is ambiguous: "
            f"{family_name} "
            f"(multiple source views)"
        )

    if len(table_candidates) > 1:
        raise ValueError(
            "Managed object family is ambiguous: "
            f"{family_name} "
            f"(multiple live tables)"
        )

    if len(published_candidates) > 1:
        raise ValueError(
            "Managed object family is ambiguous: "
            f"{family_name} "
            f"(multiple published views)"
        )

    # The published schema supplied by proposed SQL may be a
    # placeholder. Only use it when it actually matches.
    selected_published = None

    if published_schema:
        schema_matches = [
            candidate
            for candidate in published_candidates
            if candidate[1] == published_schema
        ]

        if len(schema_matches) == 1:
            selected_published = schema_matches[0]

    if selected_published is None and len(
        published_candidates
    ) == 1:
        selected_published = published_candidates[0]

    source = (
        source_candidates[0]
        if len(source_candidates) == 1
        else None
    )

    table = (
        table_candidates[0]
        if len(table_candidates) == 1
        else None
    )

    if source is None and table is None and selected_published is None:
        return None

    return ManagedObjectFamily(
        name=family_name,

        source_view_oid=(
            source[0]
            if source is not None
            else None
        ),
        source_view_schema=(
            source[1]
            if source is not None
            else None
        ),
        source_view_name=(
            source[2]
            if source is not None
            else None
        ),

        live_table_oid=(
            table[0]
            if table is not None
            else None
        ),
        live_table_schema=(
            table[1]
            if table is not None
            else None
        ),
        live_table_name=(
            table[2]
            if table is not None
            else None
        ),

        published_view_oid=(
            selected_published[0]
            if selected_published is not None
            else None
        ),
        published_view_schema=(
            selected_published[1]
            if selected_published is not None
            else None
        ),
        published_view_name=(
            selected_published[2]
            if selected_published is not None
            else None
        ),

        source_view_count=len(
            source_candidates
        ),
        live_table_count=len(
            table_candidates
        ),
        published_view_count=(
            len(published_candidates)
        ),
    )

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

    if matches:
        if published_schema:
            schema_matches = [
                family
                for family in matches
                if family.published_view_schema
                == published_schema
            ]

            if len(schema_matches) == 1:
                family = schema_matches[0]

                if family.status == FamilyStatus.AMBIGUOUS:
                    raise ValueError(
                        f"Managed object family is ambiguous: "
                        f"{family_name}"
                    )

                return family

            if len(schema_matches) > 1:
                raise ValueError(
                    "Managed object family is ambiguous: "
                    f"{family_name} "
                    f"(multiple published views in schema "
                    f"{published_schema})"
                )

            # The proposed SQL schema may be a placeholder.
            public_matches = [
                family
                for family in matches
                if family.published_view_schema
                == "public"
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

        elif len(matches) == 1:
            family = matches[0]

            if family.status == FamilyStatus.AMBIGUOUS:
                raise ValueError(
                    f"Managed object family is ambiguous: "
                    f"{family_name}"
                )

            return family

        else:
            raise ValueError(
                f"Managed object family is ambiguous: "
                f"{family_name}"
            )

    # ------------------------------------------------------------------
    # Generic fallback.
    #
    # This supports families outside auto_views/public, such as:
    #
    #   family_migration_test.t_orders
    #   family_migration_test.v_orders
    #   family_migration_test.mv_orders
    #
    # It also supports arbitrary schemas.
    # ------------------------------------------------------------------

    generic_family = _discover_generic_family(
        db,
        family_name,
        published_schema=published_schema,
    )

    if generic_family is not None:
        if generic_family.status == FamilyStatus.AMBIGUOUS:
            raise ValueError(
                f"Managed object family is ambiguous: "
                f"{family_name}"
            )

        return generic_family

    raise ValueError(
        f"Managed object family not found: {family_name}"
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

GENERIC_OBJECT_LOOKUP_SQL = """
SELECT
    c.oid,
    n.nspname AS schema_name,
    c.relname AS object_name,
    c.relkind
FROM pg_class c
JOIN pg_namespace n
    ON n.oid = c.relnamespace
WHERE c.relname = ANY(%s)
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND n.nspname NOT IN (
      'pg_catalog',
      'information_schema'
  )
ORDER BY
    c.relname,
    n.nspname;
"""


def _resolve_proposed_objects_by_catalog(
    db: Database,
    family_name: str,
    proposed_objects: list[ProposedObject],
) -> list[tuple[ProposedObject, int]]:
    """
    Resolve proposed objects directly from PostgreSQL when the
    legacy managed-family discovery cannot identify the family.

    This supports families located in arbitrary schemas, while
    still using PostgreSQL as the source of truth.

    A schema supplied by the proposed SQL is preferred when it
    identifies an existing object. Otherwise, the object name
    must resolve uniquely across non-system schemas.
    """
    names = [
        proposed.name
        for proposed in proposed_objects
    ]

    rows = db.query(
        GENERIC_OBJECT_LOOKUP_SQL,
        (names,),
    )

    candidates_by_name: dict[
        str,
        list[tuple[int, str, str]],
    ] = {}

    for (
        oid,
        schema_name,
        object_name,
        _relkind,
    ) in rows:
        candidates_by_name.setdefault(
            object_name,
            [],
        ).append(
            (
                oid,
                schema_name,
                object_name,
            )
        )

    resolved = []

    for proposed in proposed_objects:
        candidates = candidates_by_name.get(
            proposed.name,
            [],
        )

        if proposed.schema:
            schema_candidates = [
                candidate
                for candidate in candidates
                if candidate[1] == proposed.schema
            ]

            if len(schema_candidates) == 1:
                candidates = schema_candidates

        if len(candidates) == 0:
            raise ValueError(
                "Migration object could not be resolved in "
                "the database: "
                f"{proposed.name}"
            )

        if len(candidates) > 1:
            locations = ", ".join(
                f"{candidate[1]}.{candidate[2]}"
                for candidate in candidates
            )

            raise ValueError(
                "Migration object is ambiguous: "
                f"{proposed.name} "
                f"({locations})"
            )

        resolved.append(
            (
                proposed,
                candidates[0][0],
            )
        )

    return resolved

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

    try:
        family = get_managed_family(
            db,
            family_name,
            published_schema=published_schema,
        )

    except ValueError as exc:
        if "not found" not in str(exc).lower():
            raise

        # Backwards-compatible fallback:
        #
        # The objects may form a valid migration family but live
        # outside the legacy auto_views/public naming convention.
        #
        # Resolve them directly from PostgreSQL metadata instead
        # of assuming a particular schema.
        return _resolve_proposed_objects_by_catalog(
            db,
            family_name,
            proposed_objects,
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