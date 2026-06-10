import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# DATA_DIR holds the SQLite db and uploads. Locally it's ./data;
# on Render point it at the persistent disk mount (e.g. /var/data).
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "crewed.db"

_secret_file = DATA_DIR / "secret_key.txt"
if os.environ.get("SECRET_KEY"):
    SECRET_KEY = os.environ["SECRET_KEY"]
elif _secret_file.exists():
    SECRET_KEY = _secret_file.read_text().strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    _secret_file.write_text(SECRET_KEY)
