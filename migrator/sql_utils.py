def quote_identifier(identifier: str) -> str:
    """
    Quote a PostgreSQL identifier safely.

    Double quotes inside an identifier are escaped
    by doubling them.
    """
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def qualified_name(
    schema: str,
    name: str,
) -> str:
    return (
        f"{quote_identifier(schema)}."
        f"{quote_identifier(name)}"
    )