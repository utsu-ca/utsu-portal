"""
Internal integrity check. It does not decide anything about an application; it looks for a state that should not
happen (more than one verified application for the same student, and for SABP the same term) and logs it. Log lines
start with "INTEGRITY".
"""
import logging
import sqlite3

logger = logging.getLogger("utsu.integrity")


def duplicate_verified(conn: sqlite3.Connection, table: str, program: str,
                       group_columns: tuple[str, ...] = ("student_uuid",)) -> list[tuple]:
    """Students with more than one verified application (per term for SABP): there should be exactly one."""
    columns = ", ".join(group_columns)
    found = conn.execute(f"SELECT {columns}, COUNT(*) FROM {table} WHERE status = 'verified' "
                         f"AND {group_columns[0]} IS NOT NULL GROUP BY {columns} HAVING COUNT(*) > 1").fetchall()
    for *key, count in found:
        logger.error(f"INTEGRITY: {program} has {count} verified applications for one student ({', '.join(map(str, key))}); "
                     f"there should be one. A person needs to investigate.")
    return found
