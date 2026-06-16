import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

_secret_file = DATA_DIR / "secret_key.txt"
if os.environ.get("SECRET_KEY"):
    SECRET_KEY = os.environ["SECRET_KEY"]
elif _secret_file.exists():
    SECRET_KEY = _secret_file.read_text().strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    _secret_file.write_text(SECRET_KEY)
