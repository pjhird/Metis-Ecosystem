"""SQLite implementation of the operational-state contract."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


DEFAULT_MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")
MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{3})_[a-z0-9_]+\.sql$")


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
