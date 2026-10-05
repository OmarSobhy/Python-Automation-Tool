import pytest

from migrator.migration import (
    _parse_simple_projection_view,
    _replace_select_columns,
)
from migrator.models import Column


def test_simple_projection_is_accepted():
    definition = """
        SELECT
            id,
            customer_name
        FROM family_migration_test.t_orders
    """

    result = _parse_simple_projection_view(definition)

    assert result is not None

    selected_columns, from_clause = result

    assert selected_columns == [
        "id",
        "customer_name",
    ]

    assert from_clause == (
        "family_migration_test.t_orders"
    )


def test_simple_projection_is_rewritten():
    definition = """
        SELECT
            id,
            customer_name
        FROM family_migration_test.t_orders
    """

    columns = [
        Column(
            name="id",
            data_type="integer",
            nullable=False,
            position=1,
        ),
        Column(
            name="customer_name",
            data_type="text",
            nullable=True,
            position=2,
        ),
        Column(
            name="migration_status",
            data_type="text",
            nullable=True,
            position=3,
        ),
    ]

    result = _replace_select_columns(
        definition,
        columns,
    )

    assert result == (
        'SELECT\n'
        '    "id",\n'
        '    "customer_name",\n'
        '    "migration_status"\n'
        'FROM family_migration_test.t_orders'
    )


def test_expression_view_is_rejected():
    definition = """
        SELECT
            id,
            amount * 1.2 AS adjusted_amount
        FROM family_migration_test.t_orders
    """

    with pytest.raises(
        ValueError,
        match="not a simple projection",
    ):
        _replace_select_columns(
            definition,
            [
                Column(
                    name="id",
                    data_type="integer",
                    nullable=False,
                    position=1,
                ),
            ],
        )


def test_join_view_is_rejected():
    definition = """
        SELECT
            t.id,
            c.customer_name
        FROM family_migration_test.t_orders t
        JOIN customers c
            ON c.id = t.customer_id
    """

    with pytest.raises(
        ValueError,
        match="not a simple projection",
    ):
        _replace_select_columns(
            definition,
            [
                Column(
                    name="id",
                    data_type="integer",
                    nullable=False,
                    position=1,
                ),
            ],
        )


def test_filtered_view_is_rejected():
    definition = """
        SELECT
            id,
            customer_name
        FROM family_migration_test.t_orders
        WHERE active = true
    """

    with pytest.raises(
        ValueError,
        match="not a simple projection",
    ):
        _replace_select_columns(
            definition,
            [
                Column(
                    name="id",
                    data_type="integer",
                    nullable=False,
                    position=1,
                ),
            ],
        )


def test_case_expression_is_rejected():
    definition = """
        SELECT
            id,
            CASE
                WHEN status = 'X' THEN 1
                ELSE 0
            END AS is_special
        FROM family_migration_test.t_orders
    """

    with pytest.raises(
        ValueError,
        match="not a simple projection",
    ):
        _replace_select_columns(
            definition,
            [
                Column(
                    name="id",
                    data_type="integer",
                    nullable=False,
                    position=1,
                ),
            ],
        )
