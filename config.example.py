BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

# Optional VLE / WAHA settings.
# Fill these in locally; keep the real values out of git.
VLE_BASE_URL = "https://vle.example.edu.my"
VLE_EMAIL = "your-email@example.com"
VLE_PASSWORD = "your-password"
WAHA_URL = "http://localhost:2785"
WAHA_SESSION = "default"
WAHA_API_KEY = "YOUR_WAHA_API_KEY"
WAHA_PAIR_NUMBER = ""

# Optional project-specific mappings.
# Leave VLE_COURSES empty to auto-discover visible course links.
# Use local config.py for real values.
VLE_COURSES = {
    "COURSE_CODE_1": "COURSE_NAME_1",
}
WHATSAPP_MONITORED_GROUP_ALIASES = (
    "course name",
    "project group",
)
BACKFILL_MONITORED_CHAT_IDS = {}
BACKFILL_COURSE_KEYWORDS = (
    "course name",
    "project group",
)
