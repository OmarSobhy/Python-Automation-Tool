import os

from dotenv import load_dotenv
import psycopg


load_dotenv()


class Database:
    def __init__(
        self,
        conninfo: str | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ):
        conninfo = (
            conninfo
            or os.getenv("DATABASE_URL")
        )

        if conninfo:
            self.connection = psycopg.connect(
                conninfo
            )
            return

        self.connection = psycopg.connect(
            host=host or os.getenv(
                "PGHOST",
                "localhost",
            ),
            port=port or int(
                os.getenv(
                    "PGPORT",
                    "5433",
                )
            ),
            dbname=database or os.getenv(
                "PGDATABASE",
                "testdb",
            ),
            user=user or os.getenv(
                "PGUSER",
                "postgres",
            ),
            password=password or os.getenv(
                "PGPASSWORD",
                "",
            ),
        )

    def query(self, sql, params=None):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)

            if cursor.description is None:
                return []

            return cursor.fetchall()

    def close(self):
        self.connection.close()