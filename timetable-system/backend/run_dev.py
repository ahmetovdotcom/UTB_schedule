"""Local HTTP development server. Production should use uvicorn with HTTPS proxy."""
import os
from pathlib import Path

import uvicorn


if __name__ == "__main__":
    # Use the same database regardless of the directory this command runs from.
    os.chdir(Path(__file__).resolve().parent)
    # This launcher explicitly opts into HTTP cookies and binds only to loopback.
    os.environ["AUTH_COOKIE_SECURE"] = "false"
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
