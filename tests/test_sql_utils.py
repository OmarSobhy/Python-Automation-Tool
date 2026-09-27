from migrator.sql_utils import (
    qualified_name,
    quote_identifier,
)


def test_quote_identifier():
    assert quote_identifier("customer") == '"customer"'


def test_quote_identifier_escapes_quotes():
    assert (
        quote_identifier('customer"view')
        == '"customer""view"'
    )


def test_qualified_name():
    assert (
        qualified_name("demo", "v_customer")
        == '"demo"."v_customer"'
    )


def test_qualified_name_with_special_characters():
    assert (
        qualified_name(
            "My Schema",
            'Customer"View',
        )
        == '"My Schema"."Customer""View"'
    )
