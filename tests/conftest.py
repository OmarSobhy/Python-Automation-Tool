import os

import pytest
from dotenv import load_dotenv

from migrator.db import Database


@pytest.fixture
def db():
    load_dotenv(override=True)

    database = Database(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5433")),
        database=os.getenv("PGDATABASE", "testdb"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", ""),
    )

    try:
        yield database
    finally:
        database.close()
