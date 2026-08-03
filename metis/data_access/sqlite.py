"""SQLite implementation of the operational-state contract."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

from .contracts import (
    ApprovalRecord,
    ClassificationRecord,
    IntakeRecord,
    IntakeRegistrationResult,
    IntakeRegistrationStatus,
    ProposalRecord,
    ProposalReservationRecord,
    StateStoreError,
    StateTransitionRefused,
)


DEFAULT_MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")
MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{3})_[a-z0-9_]+\.sql$")
INTAKE_COLUMNS = (
    "capture_id",
    "content_hash",
    "captured_at",
    "source_type",
    "evidence_path",
    "state",
    "state_updated_at",
    "failure_reason",
    "trace_id",
)
CLASSIFICATION_COLUMNS = (
    "classification_id",
    "capture_id",
    "candidate_type",
    "sensitivity",
    "confidence",
    "routing",
    "model_id",
    "prompt_version",
    "raw_response_path",
    "created_at",
)
PROPOSAL_COLUMNS = (
    "proposal_id",
    "capture_id",
    "classification_id",
    "note_type",
    "title",
    "body_path",
    "proposed_links",
    "evidence_refs",
    "confidence",
    "sensitivity",
    "risk_level",
    "reason",
    "uncertainties_json",
    "model_id",
    "prompt_version",
    "raw_response_path",
    "content_hash",
    "draft_note_path",
    "state",
    "created_at",
)
APPROVAL_COLUMNS = (
    "approval_id",
    "proposal_id",
    "decision",
    "approver",
    "observed_status",
    "detected_at",
    "committed_at",
    "revoked_at",
)
APPROVAL_DECISIONS = frozenset({"approved", "rejected"})
PROPOSAL_RESERVATION_COLUMNS = (
    "proposal_id",
    "capture_id",
    "classification_id",
    "lease_token",
    "reserved_at",
    "lease_expires_at",
)
RESERVATION_FAILURE_REASONS = frozenset(
    {
        "proposal.proposal_consistency_failed",
        "proposal.model_configuration_failed",
        "proposal.model_request_failed",
        "proposal.model_response_refused",
        "proposal.model_response_truncated",
        "proposal.model_response_invalid",
        "proposal.proposal_evidence_failed",
        "proposal.proposal_content_failed",
        "proposal.proposal_persistence_failed",
    }
)
DRAFT_FAILURE_REASONS = frozenset(
    {
        "proposal.draft_write_failed",
        "proposal.draft_collision",
        "proposal.proposal_persistence_failed",
    }
)


def _parse_utc_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("timestamp is not canonical UTC")
    return parsed


def _has_full_proposal_lease(reservation: ProposalReservationRecord) -> bool:
    try:
        return (
            _parse_utc_timestamp(reservation.lease_expires_at)
            - _parse_utc_timestamp(reservation.reserved_at)
            == timedelta(minutes=15)
        )
    except (TypeError, ValueError):
        return False


def _timestamp_not_before(value: str, minimum: str) -> bool:
    try:
        return _parse_utc_timestamp(value) >= _parse_utc_timestamp(minimum)
    except (TypeError, ValueError):
        return False


def _intake_record(row: tuple) -> IntakeRecord:
    return IntakeRecord(*row)


def _classification_record(row: tuple) -> ClassificationRecord:
    return ClassificationRecord(*row)


def _proposal_record(row: tuple) -> ProposalRecord:
    return ProposalRecord(*row)


def _proposal_reservation_record(row: tuple) -> ProposalReservationRecord:
    return ProposalReservationRecord(*row)


class MigrationError(RuntimeError):
    """Raised when a versioned schema migration cannot be applied."""


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path


class SQLiteStateStore:
    """Manage one SQLite operational-state database."""

    def __init__(
        self,
        database_path: Path,
        *,
        migrations_directory: Path = DEFAULT_MIGRATIONS_DIRECTORY,
    ) -> None:
        self._database_path = Path(database_path)
        self._migrations_directory = Path(migrations_directory)
        self._connection: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self._database_path)
            self._connection.execute("PRAGMA foreign_keys = ON")
        return self._connection

    @property
    def schema_version(self) -> int:
        row = self._connect().execute("PRAGMA user_version").fetchone()
        return int(row[0])

    @property
    def foreign_keys_enabled(self) -> bool:
        row = self._connect().execute("PRAGMA foreign_keys").fetchone()
        return bool(row[0])

    def initialize(self) -> None:
        migrations = self._migrations()
        current_version = self.schema_version
        latest_version = migrations[-1].version
        if current_version > latest_version:
            raise MigrationError(
                f"database schema version {current_version} is newer than "
                f"supported version {latest_version}"
            )

        for migration in migrations:
            if migration.version <= current_version:
                continue
            self._apply(migration)
            current_version = migration.version

    def find_intake_by_content_hash(
        self,
        content_hash: str,
    ) -> Optional[IntakeRecord]:
        """Return the intake row registered for a content hash, if one exists."""
        try:
            row = self._connect().execute(
                "SELECT capture_id, content_hash, captured_at, source_type, "
                "evidence_path, state, state_updated_at, failure_reason, trace_id "
                "FROM intake WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
        except sqlite3.Error as error:
            raise StateStoreError(f"intake lookup failed: {error}") from error
        return None if row is None else _intake_record(row)

    def find_intake_by_capture_id(
        self,
        capture_id: str,
    ) -> Optional[IntakeRecord]:
        """Return the intake row registered for a capture ID, if one exists."""
        try:
            row = self._select_intake_by_capture_id(self._connect(), capture_id)
        except sqlite3.Error as error:
            raise StateStoreError(f"intake lookup failed: {error}") from error
        return None if row is None else _intake_record(row)

    def find_classification_by_capture_id(
        self,
        capture_id: str,
    ) -> Optional[ClassificationRecord]:
        """Return the classification row for a capture ID, if one exists."""
        try:
            row = self._connect().execute(
                "SELECT classification_id, capture_id, candidate_type, sensitivity, "
                "confidence, routing, model_id, prompt_version, raw_response_path, "
                "created_at FROM classification WHERE capture_id = ?",
                (capture_id,),
            ).fetchone()
        except sqlite3.Error as error:
            raise StateStoreError(f"classification lookup failed: {error}") from error
        return None if row is None else _classification_record(row)

    def find_proposal_by_capture_id(
        self,
        capture_id: str,
    ) -> Optional[ProposalRecord]:
        """Return the proposal row for a capture ID, if one exists."""
        try:
            row = self._select_proposal_by_capture_id(
                self._connect(),
                capture_id,
            )
        except sqlite3.Error as error:
            raise StateStoreError(f"proposal lookup failed: {error}") from error
        return None if row is None else _proposal_record(row)

    def find_proposal_reservation_by_capture_id(
        self,
        capture_id: str,
    ) -> Optional[ProposalReservationRecord]:
        """Return the proposal reservation for a capture ID, if one exists."""
        try:
            row = self._select_reservation_by_capture_id(
                self._connect(),
                capture_id,
            )
        except sqlite3.Error as error:
            raise StateStoreError(f"proposal reservation lookup failed: {error}") from error
        return None if row is None else _proposal_reservation_record(row)

    def register_intake(self, record: IntakeRecord) -> IntakeRegistrationResult:
        """Register a captured intake row or return the exact existing duplicate."""
        connection: Optional[sqlite3.Connection] = None
        try:
            connection = self._connect()
            connection.execute(
                "INSERT INTO intake ("
                "capture_id, content_hash, captured_at, source_type, evidence_path, "
                "state, state_updated_at, failure_reason, trace_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(getattr(record, column) for column in INTAKE_COLUMNS),
            )
            connection.commit()
            return IntakeRegistrationResult(
                IntakeRegistrationStatus.REGISTERED,
                record,
            )
        except sqlite3.IntegrityError as error:
            if connection is not None:
                connection.rollback()
            existing = self.find_intake_by_content_hash(record.content_hash)
            if existing is not None:
                return IntakeRegistrationResult(
                    IntakeRegistrationStatus.DUPLICATE,
                    existing,
            )
            raise StateStoreError(f"intake registration failed: {error}") from error
        except sqlite3.Error as error:
            if connection is not None:
                connection.rollback()
            raise StateStoreError(f"intake registration failed: {error}") from error

    def begin_classification(
        self,
        capture_id: str,
        started_at: str,
    ) -> IntakeRecord:
        """Move a captured or classification-failed intake to classifying."""
        try:
            connection = self._connect()
        except sqlite3.Error as error:
            raise StateStoreError(f"classification start failed: {error}") from error
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select_intake_by_capture_id(connection, capture_id)
            if row is None:
                connection.rollback()
                raise StateStoreError(
                    f"classification start failed: intake not found: {capture_id}"
                )
            current = _intake_record(row)
            eligible = (
                current.state == "captured"
                and current.failure_reason is None
                and current.state_updated_at == current.captured_at
            ) or (
                current.state == "failed"
                and current.failure_reason is not None
                and current.failure_reason.startswith("classification.")
            )
            if not eligible:
                connection.rollback()
                raise StateTransitionRefused(
                    f"cannot begin classification from state {current.state}",
                    current,
                )

            cursor = connection.execute(
                "UPDATE intake SET state = 'classifying', state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? AND state = ? "
                "AND failure_reason IS ? AND state_updated_at = ?",
                (
                    started_at,
                    capture_id,
                    current.state,
                    current.failure_reason,
                    current.state_updated_at,
                ),
            )
            if cursor.rowcount != 1:
                latest_row = self._select_intake_by_capture_id(connection, capture_id)
                connection.rollback()
                if latest_row is None:
                    raise StateStoreError(
                        f"classification start failed: intake disappeared: {capture_id}"
                    )
                raise StateTransitionRefused(
                    "classification start lost its state comparison",
                    _intake_record(latest_row),
                )
            updated_row = self._select_intake_by_capture_id(connection, capture_id)
            if updated_row is None:
                connection.rollback()
                raise StateStoreError(
                    f"classification start failed: intake disappeared: {capture_id}"
                )
            connection.commit()
            return _intake_record(updated_row)
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(f"classification start failed: {error}") from error

    def complete_classification(
        self,
        record: ClassificationRecord,
    ) -> ClassificationRecord:
        """Persist a classification and its classified state in one transaction."""
        try:
            connection = self._connect()
        except sqlite3.Error as error:
            raise StateStoreError(
                f"classification completion failed: {error}"
            ) from error
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO classification ("
                "classification_id, capture_id, candidate_type, sensitivity, "
                "confidence, routing, model_id, prompt_version, raw_response_path, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(getattr(record, column) for column in CLASSIFICATION_COLUMNS),
            )
            cursor = connection.execute(
                "UPDATE intake SET state = 'classified', state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? AND state = 'classifying'",
                (record.created_at, record.capture_id),
            )
            if cursor.rowcount != 1:
                current_row = self._select_intake_by_capture_id(
                    connection, record.capture_id
                )
                connection.rollback()
                if current_row is None:
                    raise StateStoreError(
                        "classification completion failed: intake not found: "
                        f"{record.capture_id}"
                    )
                raise StateTransitionRefused(
                    "classification completion requires state classifying",
                    _intake_record(current_row),
                )
            connection.commit()
            return record
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(
                f"classification completion failed: {error}"
            ) from error

    def record_classification_failure(
        self,
        capture_id: str,
        reason: str,
        failed_at: str,
    ) -> IntakeRecord:
        """Move a classifying intake to failed with a known reason."""
        try:
            connection = self._connect()
        except sqlite3.Error as error:
            raise StateStoreError(
                f"classification failure recording failed: {error}"
            ) from error
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE intake SET state = 'failed', state_updated_at = ?, "
                "failure_reason = ? WHERE capture_id = ? AND state = 'classifying'",
                (failed_at, reason, capture_id),
            )
            if cursor.rowcount != 1:
                current_row = self._select_intake_by_capture_id(connection, capture_id)
                connection.rollback()
                if current_row is None:
                    raise StateStoreError(
                        f"classification failure recording failed: intake not found: {capture_id}"
                    )
                raise StateTransitionRefused(
                    "classification failure recording requires state classifying",
                    _intake_record(current_row),
                )
            updated_row = self._select_intake_by_capture_id(connection, capture_id)
            if updated_row is None:
                connection.rollback()
                raise StateStoreError(
                    "classification failure recording failed: intake disappeared: "
                    f"{capture_id}"
                )
            connection.commit()
            return _intake_record(updated_row)
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(
                f"classification failure recording failed: {error}"
            ) from error

    def begin_proposal(
        self,
        reservation: ProposalReservationRecord,
    ) -> ProposalReservationRecord:
        """Reserve a classified intake and enter proposing atomically."""
        if not _has_full_proposal_lease(reservation):
            raise StateStoreError("proposal reservation lease is invalid")
        connection = self._proposal_connection("proposal start")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._required_intake(connection, reservation.capture_id)
            classification = connection.execute(
                "SELECT classification_id FROM classification WHERE capture_id = ?",
                (reservation.capture_id,),
            ).fetchone()
            if (
                current.state != "classified"
                or current.failure_reason is not None
                or classification != (reservation.classification_id,)
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    f"cannot begin proposal from state {current.state}",
                    current,
                )
            connection.execute(
                "INSERT INTO proposal_reservation ("
                "proposal_id, capture_id, classification_id, lease_token, "
                "reserved_at, lease_expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                tuple(
                    getattr(reservation, column)
                    for column in PROPOSAL_RESERVATION_COLUMNS
                ),
            )
            cursor = connection.execute(
                "UPDATE intake SET state = 'proposing', state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? AND state = 'classified' "
                "AND failure_reason IS NULL AND state_updated_at = ?",
                (
                    reservation.reserved_at,
                    reservation.capture_id,
                    current.state_updated_at,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal start lost its state comparison",
                    current,
                )
            connection.commit()
            return reservation
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.IntegrityError as error:
            if connection.in_transaction:
                connection.rollback()
            current = self.find_intake_by_capture_id(reservation.capture_id)
            if current is not None:
                raise StateTransitionRefused(
                    "proposal reservation already exists or conflicts",
                    current,
                ) from error
            raise StateStoreError(f"proposal start failed: {error}") from error
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(f"proposal start failed: {error}") from error

    def reclaim_proposal(
        self,
        expected: ProposalReservationRecord,
        replacement: ProposalReservationRecord,
        reclaimed_at: str,
    ) -> ProposalReservationRecord:
        """Replace an expired reservation token and resume proposing."""
        if (
            expected.proposal_id != replacement.proposal_id
            or expected.capture_id != replacement.capture_id
            or expected.classification_id != replacement.classification_id
            or expected.lease_token == replacement.lease_token
        ):
            raise StateStoreError("proposal reclaim identities are inconsistent")
        if (
            replacement.reserved_at != reclaimed_at
            or not _has_full_proposal_lease(replacement)
        ):
            raise StateStoreError("proposal reclaim lease is invalid")
        connection = self._proposal_connection("proposal reclaim")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._required_intake(connection, expected.capture_id)
            row = self._select_reservation_by_capture_id(
                connection,
                expected.capture_id,
            )
            reservation = (
                None if row is None else _proposal_reservation_record(row)
            )
            proposal = self._select_proposal_by_capture_id(
                connection,
                expected.capture_id,
            )
            retryable_state = (
                current.state == "proposing"
                and current.failure_reason is None
                and current.state_updated_at == expected.reserved_at
                and _has_full_proposal_lease(expected)
            ) or (
                current.state == "failed"
                and current.failure_reason in RESERVATION_FAILURE_REASONS
                and current.state_updated_at == expected.lease_expires_at
                and _timestamp_not_before(
                    expected.lease_expires_at,
                    expected.reserved_at,
                )
            )
            if (
                reservation != expected
                or proposal is not None
                or not retryable_state
                or expected.lease_expires_at > reclaimed_at
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal reservation is active or no longer matches",
                    current,
                )
            cursor = connection.execute(
                "UPDATE proposal_reservation SET lease_token = ?, reserved_at = ?, "
                "lease_expires_at = ? WHERE proposal_id = ? AND capture_id = ? "
                "AND classification_id = ? AND lease_token = ? AND reserved_at = ? "
                "AND lease_expires_at = ?",
                (
                    replacement.lease_token,
                    replacement.reserved_at,
                    replacement.lease_expires_at,
                    expected.proposal_id,
                    expected.capture_id,
                    expected.classification_id,
                    expected.lease_token,
                    expected.reserved_at,
                    expected.lease_expires_at,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal reclaim lost its reservation comparison",
                    current,
                )
            cursor = connection.execute(
                "UPDATE intake SET state = 'proposing', state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? AND state = ? "
                "AND failure_reason IS ? AND state_updated_at = ?",
                (
                    replacement.reserved_at,
                    expected.capture_id,
                    current.state,
                    current.failure_reason,
                    current.state_updated_at,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal reclaim lost its state comparison",
                    current,
                )
            connection.commit()
            return replacement
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(f"proposal reclaim failed: {error}") from error

    def record_proposal_failure(
        self,
        capture_id: str,
        lease_token: str,
        reason: str,
        failed_at: str,
    ) -> IntakeRecord:
        """Fail a proposing intake while retaining an expired reservation."""
        if reason not in RESERVATION_FAILURE_REASONS:
            raise StateStoreError("proposal failure reason is outside its namespace")
        connection = self._proposal_connection("proposal failure recording")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._required_intake(connection, capture_id)
            reservation_row = self._select_reservation_by_capture_id(
                connection,
                capture_id,
            )
            reservation = (
                None
                if reservation_row is None
                else _proposal_reservation_record(reservation_row)
            )
            proposal = self._select_proposal_by_capture_id(connection, capture_id)
            if (
                current.state != "proposing"
                or current.failure_reason is not None
                or reservation is None
                or reservation.lease_token != lease_token
                or current.state_updated_at != reservation.reserved_at
                or not _has_full_proposal_lease(reservation)
                or not _timestamp_not_before(failed_at, reservation.reserved_at)
                or proposal is not None
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal failure requires the current proposing lease",
                    current,
                )
            cursor = connection.execute(
                "UPDATE proposal_reservation SET lease_expires_at = ? "
                "WHERE capture_id = ? AND lease_token = ?",
                (failed_at, capture_id, lease_token),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal failure lost its lease comparison",
                    current,
                )
            cursor = connection.execute(
                "UPDATE intake SET state = 'failed', state_updated_at = ?, "
                "failure_reason = ? WHERE capture_id = ? AND state = 'proposing' "
                "AND failure_reason IS NULL AND state_updated_at = ?",
                (failed_at, reason, capture_id, reservation.reserved_at),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal failure lost its state comparison",
                    current,
                )
            updated = self._required_intake(connection, capture_id)
            connection.commit()
            return updated
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(
                f"proposal failure recording failed: {error}"
            ) from error

    def complete_proposal(
        self,
        record: ProposalRecord,
        lease_token: str,
    ) -> ProposalRecord:
        """Persist a proposal, consume its reservation, and enter proposed."""
        connection = self._proposal_connection("proposal completion")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._required_intake(connection, record.capture_id)
            reservation_row = self._select_reservation_by_capture_id(
                connection,
                record.capture_id,
            )
            reservation = (
                None
                if reservation_row is None
                else _proposal_reservation_record(reservation_row)
            )
            if (
                current.state != "proposing"
                or current.failure_reason is not None
                or reservation is None
                or reservation.proposal_id != record.proposal_id
                or reservation.classification_id != record.classification_id
                or reservation.lease_token != lease_token
                or current.state_updated_at != reservation.reserved_at
                or not _has_full_proposal_lease(reservation)
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal completion requires the current proposing lease",
                    current,
                )
            connection.execute(
                "INSERT INTO proposal ("
                "proposal_id, capture_id, classification_id, note_type, title, "
                "body_path, proposed_links, evidence_refs, confidence, sensitivity, "
                "risk_level, reason, uncertainties_json, model_id, prompt_version, "
                "raw_response_path, content_hash, draft_note_path, state, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(getattr(record, column) for column in PROPOSAL_COLUMNS),
            )
            deleted = connection.execute(
                "DELETE FROM proposal_reservation WHERE capture_id = ? "
                "AND proposal_id = ? AND lease_token = ?",
                (record.capture_id, record.proposal_id, lease_token),
            )
            if deleted.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal completion lost its lease comparison",
                    current,
                )
            cursor = connection.execute(
                "UPDATE intake SET state = 'proposed', state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? AND state = 'proposing' "
                "AND failure_reason IS NULL AND state_updated_at = ?",
                (record.created_at, record.capture_id, reservation.reserved_at),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal completion lost its state comparison",
                    current,
                )
            connection.commit()
            return record
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(f"proposal completion failed: {error}") from error

    def record_draft_failure(
        self,
        capture_id: str,
        proposal_id: str,
        reason: str,
        failed_at: str,
    ) -> IntakeRecord:
        """Fail an intake after its proposal row was safely persisted."""
        if reason not in DRAFT_FAILURE_REASONS:
            raise StateStoreError("draft failure reason is outside proposal namespace")
        connection = self._proposal_connection("draft failure recording")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._required_intake(connection, capture_id)
            proposal_row = self._select_proposal_by_capture_id(connection, capture_id)
            proposal = None if proposal_row is None else _proposal_record(proposal_row)
            reservation = self._select_reservation_by_capture_id(connection, capture_id)
            if (
                current.state != "proposed"
                or current.failure_reason is not None
                or proposal is None
                or proposal.proposal_id != proposal_id
                or proposal.draft_note_path is not None
                or proposal.state != "pending"
                or reservation is not None
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    "draft failure requires an unregistered proposed draft",
                    current,
                )
            cursor = connection.execute(
                "UPDATE intake SET state = 'failed', state_updated_at = ?, "
                "failure_reason = ? WHERE capture_id = ? AND state = 'proposed' "
                "AND failure_reason IS NULL AND state_updated_at = ?",
                (failed_at, reason, capture_id, current.state_updated_at),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "draft failure lost its state comparison",
                    current,
                )
            updated = self._required_intake(connection, capture_id)
            connection.commit()
            return updated
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(
                f"draft failure recording failed: {error}"
            ) from error

    def resume_proposal_draft(
        self,
        capture_id: str,
        proposal_id: str,
        resumed_at: str,
    ) -> IntakeRecord:
        """Restore a valid proposal-stage failure to proposed."""
        connection = self._proposal_connection("proposal draft resume")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._required_intake(connection, capture_id)
            proposal_row = self._select_proposal_by_capture_id(connection, capture_id)
            proposal = None if proposal_row is None else _proposal_record(proposal_row)
            reservation = self._select_reservation_by_capture_id(connection, capture_id)
            if (
                current.state != "failed"
                or current.failure_reason not in DRAFT_FAILURE_REASONS
                or proposal is None
                or proposal.proposal_id != proposal_id
                or proposal.draft_note_path is not None
                or proposal.state != "pending"
                or reservation is not None
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal draft resume requires a valid proposal failure",
                    current,
                )
            cursor = connection.execute(
                "UPDATE intake SET state = 'proposed', state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? AND state = 'failed' "
                "AND failure_reason = ? AND state_updated_at = ?",
                (
                    resumed_at,
                    capture_id,
                    current.failure_reason,
                    current.state_updated_at,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "proposal draft resume lost its state comparison",
                    current,
                )
            updated = self._required_intake(connection, capture_id)
            connection.commit()
            return updated
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(f"proposal draft resume failed: {error}") from error

    def register_proposal_draft(
        self,
        capture_id: str,
        proposal_id: str,
        draft_note_path: str,
        registered_at: str,
    ) -> ProposalRecord:
        """Register an exact draft and enter awaiting approval atomically."""
        connection = self._proposal_connection("proposal draft registration")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._required_intake(connection, capture_id)
            proposal_row = self._select_proposal_by_capture_id(connection, capture_id)
            proposal = None if proposal_row is None else _proposal_record(proposal_row)
            reservation = self._select_reservation_by_capture_id(connection, capture_id)
            if (
                current.state != "proposed"
                or current.failure_reason is not None
                or proposal is None
                or proposal.proposal_id != proposal_id
                or proposal.draft_note_path is not None
                or proposal.state != "pending"
                or reservation is not None
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    "draft registration requires an unregistered proposed draft",
                    current,
                )
            cursor = connection.execute(
                "UPDATE proposal SET draft_note_path = ? WHERE proposal_id = ? "
                "AND capture_id = ? AND draft_note_path IS NULL",
                (draft_note_path, proposal_id, capture_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "draft registration lost its proposal comparison",
                    current,
                )
            cursor = connection.execute(
                "UPDATE intake SET state = 'awaiting_approval', state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? AND state = 'proposed' "
                "AND failure_reason IS NULL AND state_updated_at = ?",
                (registered_at, capture_id, current.state_updated_at),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "draft registration lost its state comparison",
                    current,
                )
            updated_row = self._select_proposal_by_capture_id(connection, capture_id)
            if updated_row is None:
                connection.rollback()
                raise StateStoreError("registered proposal disappeared")
            updated = _proposal_record(updated_row)
            connection.commit()
            return updated
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(
                f"proposal draft registration failed: {error}"
            ) from error

    def find_intakes_awaiting_approval(self) -> Tuple[IntakeRecord, ...]:
        """Return every intake whose draft is waiting for a human decision."""
        try:
            rows = self._connect().execute(
                "SELECT capture_id, content_hash, captured_at, source_type, "
                "evidence_path, state, state_updated_at, failure_reason, trace_id "
                "FROM intake WHERE state = 'awaiting_approval' "
                "ORDER BY state_updated_at, capture_id"
            ).fetchall()
        except sqlite3.Error as error:
            raise StateStoreError(f"approval queue lookup failed: {error}") from error
        return tuple(_intake_record(row) for row in rows)

    def record_approval(self, record: ApprovalRecord) -> IntakeRecord:
        """Record one human decision and transition its intake atomically."""
        if (
            record.decision not in APPROVAL_DECISIONS
            or record.observed_status != record.decision
            or not record.approver.startswith("human:")
            or record.committed_at is not None
            or record.revoked_at is not None
        ):
            raise StateStoreError("approval record is inconsistent")
        connection = self._proposal_connection("approval recording")
        try:
            connection.execute("BEGIN IMMEDIATE")
            proposal_row = connection.execute(
                "SELECT capture_id, draft_note_path, state FROM proposal "
                "WHERE proposal_id = ?",
                (record.proposal_id,),
            ).fetchone()
            if proposal_row is None:
                connection.rollback()
                raise StateStoreError(
                    f"approval recording failed: proposal not found: "
                    f"{record.proposal_id}"
                )
            capture_id, draft_note_path, proposal_state = proposal_row
            current = self._required_intake(connection, capture_id)
            approved_already = connection.execute(
                "SELECT COUNT(*) FROM approval WHERE proposal_id = ?",
                (record.proposal_id,),
            ).fetchone()
            if (
                current.state != "awaiting_approval"
                or current.failure_reason is not None
                or proposal_state != "pending"
                or draft_note_path is None
                or approved_already != (0,)
                or not _timestamp_not_before(
                    record.detected_at,
                    current.state_updated_at,
                )
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    "approval requires a pending proposal awaiting a decision",
                    current,
                )
            connection.execute(
                "INSERT INTO approval (approval_id, proposal_id, decision, approver, "
                "observed_status, detected_at, committed_at, revoked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(getattr(record, column) for column in APPROVAL_COLUMNS),
            )
            cursor = connection.execute(
                "UPDATE proposal SET state = ? WHERE proposal_id = ? "
                "AND capture_id = ? AND state = 'pending'",
                (record.decision, record.proposal_id, capture_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "approval lost its proposal comparison",
                    current,
                )
            cursor = connection.execute(
                "UPDATE intake SET state = ?, state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? "
                "AND state = 'awaiting_approval' AND failure_reason IS NULL "
                "AND state_updated_at = ?",
                (
                    record.decision,
                    record.detected_at,
                    capture_id,
                    current.state_updated_at,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "approval lost its state comparison",
                    current,
                )
            updated = self._required_intake(connection, capture_id)
            connection.commit()
            return updated
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(f"approval recording failed: {error}") from error

    def find_approval_by_proposal_id(
        self,
        proposal_id: str,
    ) -> Optional[ApprovalRecord]:
        """Return the recorded decision for a proposal, if one exists."""
        try:
            row = self._connect().execute(
                f"SELECT {', '.join(APPROVAL_COLUMNS)} FROM approval "
                "WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        except sqlite3.Error as error:
            raise StateStoreError(f"approval lookup failed: {error}") from error
        return None if row is None else ApprovalRecord(*row)

    def record_filing(
        self,
        capture_id: str,
        proposal_id: str,
        approval_id: str,
        committed_at: str,
    ) -> IntakeRecord:
        """Commit an approved note and mark its intake filed atomically."""
        connection = self._proposal_connection("filing")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = self._required_intake(connection, capture_id)
            proposal_row = connection.execute(
                "SELECT proposal_id, state FROM proposal WHERE capture_id = ?",
                (capture_id,),
            ).fetchone()
            approval_row = connection.execute(
                "SELECT proposal_id, decision, committed_at, revoked_at "
                "FROM approval WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if (
                current.state != "approved"
                or current.failure_reason is not None
                or proposal_row != (proposal_id, "approved")
                or approval_row != (proposal_id, "approved", None, None)
                or not _timestamp_not_before(committed_at, current.state_updated_at)
            ):
                connection.rollback()
                raise StateTransitionRefused(
                    "filing requires an approved intake with an uncommitted decision",
                    current,
                )
            cursor = connection.execute(
                "UPDATE approval SET committed_at = ? WHERE approval_id = ? "
                "AND proposal_id = ? AND committed_at IS NULL",
                (committed_at, approval_id, proposal_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "filing lost its approval comparison",
                    current,
                )
            cursor = connection.execute(
                "UPDATE intake SET state = 'filed', state_updated_at = ?, "
                "failure_reason = NULL WHERE capture_id = ? AND state = 'approved' "
                "AND failure_reason IS NULL AND state_updated_at = ?",
                (committed_at, capture_id, current.state_updated_at),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise StateTransitionRefused(
                    "filing lost its state comparison",
                    current,
                )
            updated = self._required_intake(connection, capture_id)
            connection.commit()
            return updated
        except (StateStoreError, StateTransitionRefused):
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise StateStoreError(f"filing failed: {error}") from error

    def _proposal_connection(self, operation: str) -> sqlite3.Connection:
        try:
            return self._connect()
        except sqlite3.Error as error:
            raise StateStoreError(f"{operation} failed: {error}") from error

    def _required_intake(
        self,
        connection: sqlite3.Connection,
        capture_id: str,
    ) -> IntakeRecord:
        row = self._select_intake_by_capture_id(connection, capture_id)
        if row is None:
            raise StateStoreError(f"intake not found: {capture_id}")
        return _intake_record(row)

    def _select_proposal_by_capture_id(
        self,
        connection: sqlite3.Connection,
        capture_id: str,
    ) -> Optional[tuple]:
        return connection.execute(
            "SELECT proposal_id, capture_id, classification_id, note_type, title, "
            "body_path, proposed_links, evidence_refs, confidence, sensitivity, "
            "risk_level, reason, uncertainties_json, model_id, prompt_version, "
            "raw_response_path, content_hash, draft_note_path, state, created_at "
            "FROM proposal WHERE capture_id = ?",
            (capture_id,),
        ).fetchone()

    def _select_reservation_by_capture_id(
        self,
        connection: sqlite3.Connection,
        capture_id: str,
    ) -> Optional[tuple]:
        return connection.execute(
            "SELECT proposal_id, capture_id, classification_id, lease_token, "
            "reserved_at, lease_expires_at FROM proposal_reservation "
            "WHERE capture_id = ?",
            (capture_id,),
        ).fetchone()

    def _select_intake_by_capture_id(
        self,
        connection: sqlite3.Connection,
        capture_id: str,
    ) -> Optional[tuple]:
        return connection.execute(
            "SELECT capture_id, content_hash, captured_at, source_type, "
            "evidence_path, state, state_updated_at, failure_reason, trace_id "
            "FROM intake WHERE capture_id = ?",
            (capture_id,),
        ).fetchone()

    def _migrations(self) -> Tuple[Migration, ...]:
        if not self._migrations_directory.is_dir():
            raise MigrationError(
                f"migration directory does not exist: {self._migrations_directory}"
            )

        migrations = []
        versions = set()
        for path in sorted(self._migrations_directory.glob("*.sql")):
            match = MIGRATION_NAME.fullmatch(path.name)
            if match is None:
                raise MigrationError(f"invalid migration filename: {path.name}")
            version = int(match.group("version"))
            if version in versions:
                raise MigrationError(f"duplicate migration version: {version}")
            versions.add(version)
            migrations.append(Migration(version=version, path=path))

        if not migrations:
            raise MigrationError("no migrations found")

        for expected_version, migration in enumerate(migrations, start=1):
            if migration.version != expected_version:
                raise MigrationError(
                    f"expected migration {expected_version:03d}, "
                    f"found {migration.path.name}"
                )

        return tuple(migrations)

    def _apply(self, migration: Migration) -> None:
        connection = self._connect()
        try:
            script = migration.path.read_text()
            transaction = (
                "BEGIN IMMEDIATE;\n"
                f"{script}\n"
                f"PRAGMA user_version = {migration.version};\n"
                "COMMIT;"
            )
            connection.executescript(transaction)
        except (OSError, sqlite3.Error) as error:
            if connection.in_transaction:
                connection.rollback()
            raise MigrationError(
                f"migration {migration.path.name} failed: {error}"
            ) from error

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "SQLiteStateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
