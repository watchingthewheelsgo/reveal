import unittest

from server.db.engine import normalize_database_url


class DatabaseEngineTest(unittest.TestCase):
    def test_plain_postgres_url_uses_asyncpg_and_ssl(self):
        url = normalize_database_url(
            "postgresql://postgres:secret@db.example.supabase.co:5432/postgres"
        )

        self.assertEqual(url.drivername, "postgresql+asyncpg")
        self.assertEqual(url.username, "postgres")
        self.assertEqual(url.password, "secret")
        self.assertEqual(url.host, "db.example.supabase.co")
        self.assertEqual(url.port, 5432)
        self.assertEqual(url.database, "postgres")
        self.assertEqual(url.query["ssl"], "require")

    def test_asyncpg_url_translates_sslmode_for_sqlalchemy(self):
        url = normalize_database_url(
            "postgresql+asyncpg://postgres:secret@db.example.supabase.co/postgres?sslmode=require"
        )

        self.assertEqual(url.drivername, "postgresql+asyncpg")
        self.assertEqual(url.query["ssl"], "require")
        self.assertNotIn("sslmode", url.query)

    def test_sqlite_url_is_left_unchanged(self):
        url = normalize_database_url("sqlite+aiosqlite:///./data/reveal.db")

        self.assertEqual(url.drivername, "sqlite+aiosqlite")
        self.assertEqual(url.database, "./data/reveal.db")

    def test_supabase_transaction_pooler_disables_prepared_statement_cache(self):
        url = normalize_database_url(
            "postgresql://postgres.example:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
        )

        self.assertEqual(url.drivername, "postgresql+asyncpg")
        self.assertEqual(url.query["ssl"], "require")
        self.assertEqual(url.query["prepared_statement_cache_size"], "0")


if __name__ == "__main__":
    unittest.main()
