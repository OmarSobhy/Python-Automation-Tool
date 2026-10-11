from migrator.snapshot import get_object


def test_get_object_captures_grants(db):
    rows = db.query(
        """
        SELECT c.oid
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'mv_repayments_info'
          AND c.relkind IN ('v', 'm')
        """
    )

    assert rows, "public.mv_repayments_info was not found"

    obj = get_object(db, rows[0][0])

    assert obj.grants is not None
    assert isinstance(obj.grants, list)