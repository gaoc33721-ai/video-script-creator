import os
import sys
import urllib.request


runtime = os.getenv("APP_RUNTIME", "streamlit").strip().lower()
port = os.getenv("PORT") or os.getenv("STREAMLIT_SERVER_PORT") or "8501"
path = "/healthz" if runtime in {"api", "fastapi", "http"} else "/_stcore/health"
url = f"http://127.0.0.1:{port}{path}"

try:
    with urllib.request.urlopen(url, timeout=3) as response:
        if response.status >= 400:
            raise RuntimeError(f"health check returned {response.status}")
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
