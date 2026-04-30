import os
import sys


def main():
    runtime = os.getenv("APP_RUNTIME", "streamlit").strip().lower()
    port = os.getenv("PORT") or os.getenv("STREAMLIT_SERVER_PORT") or "8501"

    if runtime in {"api", "fastapi", "http"}:
        os.execvp(
            "uvicorn",
            [
                "uvicorn",
                "api_app:app",
                "--host",
                "0.0.0.0",
                "--port",
                port,
            ],
        )

    os.execvp(
        "streamlit",
        [
            "streamlit",
            "run",
            "app.py",
            "--server.address",
            "0.0.0.0",
            "--server.port",
            port,
        ],
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
