import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DocumentationSecurityTests(unittest.TestCase):
    def test_generated_documentation_routes_are_not_public(self):
        probe = """
from fastapi.testclient import TestClient
import api_app

client = TestClient(api_app.app)
statuses = {
    path: client.get(path).status_code
    for path in ("/docs", "/docs/", "/redoc", "/redoc/", "/openapi.json", "/healthz")
}
print(statuses)
raise SystemExit(0 if statuses == {
    "/docs": 404,
    "/docs/": 404,
    "/redoc": 404,
    "/redoc/": 404,
    "/openapi.json": 404,
    "/healthz": 200,
} else 1)
"""
        with tempfile.TemporaryDirectory(prefix="documentation-security-tests-") as app_data_dir:
            environment = os.environ.copy()
            environment.update(
                {
                    "APP_DATA_DIR": app_data_dir,
                    "STORAGE_BACKEND": "local",
                    "APP_ACCESS_CONTROL_ENABLED": "false",
                }
            )
            result = subprocess.run(
                [sys.executable, "-c", probe],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
