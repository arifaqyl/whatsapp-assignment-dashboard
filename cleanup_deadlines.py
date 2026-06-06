import shutil
import sqlite3
from pathlib import Path

from paths import DEADLINES_DB as DEADLINES_DB_PATH
from deadline_utils import (
    choose_better_source,
    choose_better_task,
    is_generic_due,
    should_replace_due,
    tasks_match,
)

DB = str(DEADLINES_DB_PATH)


def cleanup():
    backup_path = Path(DB).with_suffix(".db.bak_cleanup")
    if Path(DB).exists() and not backup_path.exists():
        shutil.copy2(DB, backup_path)

    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id, task, course, due, status, source FROM deadlines WHERE status != 'Done' ORDER BY course, id"
    ).fetchall()

    deleted = 0
    updated = 0
    for i, base in enumerate(rows):
        base_id, base_task, base_course, base_due, base_status, base_source = base
        if base_id is None:
            continue
        for other in rows[i + 1:]:
            other_id, other_task, other_course, other_due, other_status, other_source = other
            if base_course != other_course:
                continue
            if not tasks_match(base_task, other_task):
                continue

            winner_id = base_id
            loser_id = other_id
            winner_task = choose_better_task(base_task, other_task)
            winner_due = base_due
            if should_replace_due(base_due, other_due, base_source, other_source):
                winner_due = other_due
            winner_source = choose_better_source(base_source, other_source)

            if is_generic_due(base_due) and not is_generic_due(other_due):
                winner_id = other_id
                loser_id = base_id
                winner_task = choose_better_task(other_task, base_task)
                winner_due = other_due
                winner_source = choose_better_source(other_source, base_source)

            conn.execute(
                "UPDATE deadlines SET task=?, due=?, source=? WHERE id=?",
                (winner_task, winner_due, winner_source, winner_id)
            )
            conn.execute("DELETE FROM deadlines WHERE id=?", (loser_id,))
            updated += 1
            deleted += 1

            if winner_id == base_id:
                base_task, base_due, base_source = winner_task, winner_due, winner_source
            else:
                base_id, base_task, base_due, base_source = winner_id, winner_task, winner_due, winner_source

    conn.commit()
    conn.close()
    return updated, deleted, str(backup_path)


if __name__ == "__main__":
    updated, deleted, backup = cleanup()
    print(f"updated={updated} deleted={deleted} backup={backup}")
