import sqlite3

DB_PATH = "/root/student-bot/deadlines.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    course TEXT NOT NULL,
    due TEXT NOT NULL,
    status TEXT NOT NULL
)
""")

cur.execute("DELETE FROM deadlines")

deadlines = [
    ("DATABASE Assignment 2", "IEB20603", "29 May", "Pending"),
    ("Tasks 3 & 4",           "IEB20603", "29 May", "Pending"),
    ("Tasks 5-9",             "IEB20603", "1 June", "Pending"),
    ("OOSAD Project",         "IEB20703", "~2-8 June", "Pending"),
    ("OOP Project",           "ISB16003", "6 June", "Pending"),
    ("Stats Report",          "IGB20303", "18 June", "Pending"),
]

cur.executemany(
    "INSERT INTO deadlines (task, course, due, status) VALUES (?, ?, ?, ?)",
    deadlines
)

conn.commit()
conn.close()
print(f"DB initialized at {DB_PATH} with {len(deadlines)} deadlines.")
