"""Test-only operational-state inspection kept inside the SQL boundary."""

from metis.data_access import ApprovalRecord


def approval_rows(store) -> list[ApprovalRecord]:
    rows = store._connection.execute(
        "SELECT approval_id, proposal_id, decision, approver, observed_status, "
        "detected_at, committed_at, revoked_at FROM approval ORDER BY approval_id"
    ).fetchall()
    return [ApprovalRecord(*row) for row in rows]


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


def force_intake_state(
    store,
    capture_id: str,
    *,
    state: str,
    state_updated_at: str,
    failure_reason: str | None,
) -> None:
    cursor = store._connection.execute(
        "UPDATE intake SET state = ?, state_updated_at = ?, failure_reason = ? "
        "WHERE capture_id = ?",
        (state, state_updated_at, failure_reason, capture_id),
    )
    if cursor.rowcount != 1:
        store._connection.rollback()
        raise AssertionError("test intake state mutation missed its row")
    store._connection.commit()


def force_proposal_draft_path(
    store,
    proposal_id: str,
    draft_note_path: str | None,
) -> None:
    cursor = store._connection.execute(
        "UPDATE proposal SET draft_note_path = ? WHERE proposal_id = ?",
        (draft_note_path, proposal_id),
    )
    if cursor.rowcount != 1:
        store._connection.rollback()
        raise AssertionError("test proposal mutation missed its row")
    store._connection.commit()


def force_proposal_reservation_timestamps(
    store,
    capture_id: str,
    *,
    reserved_at: str,
    lease_expires_at: str,
) -> None:
    cursor = store._connection.execute(
        "UPDATE proposal_reservation SET reserved_at = ?, lease_expires_at = ? "
        "WHERE capture_id = ?",
        (reserved_at, lease_expires_at, capture_id),
    )
    if cursor.rowcount != 1:
        store._connection.rollback()
        raise AssertionError("test reservation mutation missed its row")
    store._connection.commit()
