import sys
import tempfile
import unittest

from ArbDashboard.backend.process_utils import resolve_python_executable
from arbcore.database.db_manager import DatabaseManager


class ResolvePythonExecutableTest(unittest.TestCase):
    def test_prefers_current_interpreter(self):
        self.assertEqual(resolve_python_executable("missing-backend-dir"), sys.executable)


class DatabaseSchemaMigrationTest(unittest.TestCase):
    def test_futures_daily_has_runtime_price_columns(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db = DatabaseManager(db_path=f"{tmp_dir}/schema.db")
            conn = db._get_conn()
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(futures_daily)")}
            finally:
                conn.close()

        self.assertIn("close_price", columns)
        self.assertIn("volume", columns)


if __name__ == "__main__":
    unittest.main()
