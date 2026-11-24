import os
import sys

APP_DIR = "/app"
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from web_service import app as application, init_db

try:
    init_db()
except Exception as exc:
    sys.stderr.write(f"Initial schema check failed: {exc}\n")
