"""Test-only operational-state inspection kept inside the SQL boundary."""


def table_row_count(store, table: str) -> int:
    if table not in {
        "intake",
        "classification",
        "proposal",
        "proposal_reservation",
        "approval",
        "audit_event",
    }:
        raise ValueError("inspection table is not allowed")
    return store._connection.execute(
        f"SELECT COUNT(*) FROM {table}"
    ).fetchone()[0]
