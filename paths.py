from pathlib import Path


ROOT = Path(__file__).resolve().parent
MESSAGES_DB = ROOT / "messages.db"
DEADLINES_DB = ROOT / "deadlines.db"
CONFIG_FILE = ROOT / "config.py"
SESSION_FILE = ROOT / "storageState.json"
SCRAPE_LOG = ROOT / "scrape.log"
