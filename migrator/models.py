from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool
    position: int


@dataclass
class Index:
    name: str
    definition: str


@dataclass
class DatabaseObject:
    oid: int
    schema: str
    name: str
    object_type: str
    definition: str | None = None
    owner: str | None = None
    comment: str | None = None
    materialized_view_populated: bool | None = None
    columns: list[Column] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    dependencies: list[int] = field(default_factory=list)

@dataclass
class ProposedObject:
    schema: str
    name: str
    sql: str

@dataclass
class Dependency:
    referenced_oid: int
    dependent_oid: int


@dataclass
class Snapshot:
    root_oid: int
    objects: list[DatabaseObject] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)


@dataclass
class ColumnChange:
    change_type: str
    column_name: str
    old_column: Column | None = None
    new_column: Column | None = None
    change_reason: str | None = None


@dataclass
class SchemaComparison:
    added: list[ColumnChange] = field(default_factory=list)
    removed: list[ColumnChange] = field(default_factory=list)
    changed: list[ColumnChange] = field(default_factory=list)

    @property
    def is_breaking(self) -> bool:
        return bool(
            self.removed
            or self.changed
        )


@dataclass
class ImpactedObject:
    oid: int
    object_name: str
    object_type: str
    columns: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    object_name: str
    object_type: str
    column_name: str
    issue_type: str


@dataclass
class MigrationValidation:
    issues: list[ValidationIssue] = field(
        default_factory=list
    )

    @property
    def is_valid(self) -> bool:
        return not self.issues


@dataclass
class MigrationPlan:
    root_oid: int
    root_name: str
    comparison: SchemaComparison
    proposed_definitions: dict[int, str] = field(default_factory=dict)
    proposed_columns: dict[int, list[Column]] = field(
        default_factory=dict
    )
    objects: dict[int, DatabaseObject] = field(default_factory=dict)
    impacted_objects: list[ImpactedObject] = field(
        default_factory=list
    )
    drop_order: list[int] = field(
        default_factory=list
    )
    create_order: list[int] = field(
        default_factory=list
    )
    dependencies: list[Dependency] = field(
        default_factory=list
    )
    validation: MigrationValidation = field(
        default_factory=MigrationValidation
    )