"""Fresh, isolated copies of the company's Odoo database, one per episode.

The demo company in the `acme` database is frozen once as a Postgres template
(`vtgolden`). Each episode is `CREATE DATABASE vte_<n> TEMPLATE vtgolden`, which
copies the files rather than replaying a year of business, so a reset takes about
a second. A second Odoo server, on its own port, serves only `vte_*` databases,
so episodes never touch `acme` or each other, and many can run at once.

Episodes reuse a fixed set of database names ("slots"). Odoo loads its model
registry once per database name, which takes about 6 seconds; dropping and
re-creating a slot under the same name keeps that registry warm, so a reset
costs about 1 second. Odoo's cache-signalling sequences come back with the
template, so the server notices the reset and clears its caches.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg

from edp.odoo import Odoo

ROOT = Path(__file__).resolve().parent.parent
HOME = ROOT / ".odoo"
GOLDEN = "vtgolden"
PREFIX = "vte_"
PORT = int(os.environ.get("VOIGHT_ODOO_PORT", "8070"))
SOURCE_DB = os.environ.get("VOIGHT_SOURCE_DB", "acme")
USER, PASSWORD = os.environ.get("ODOO_USER", "admin"), os.environ.get("ODOO_PASSWORD", "admin")


def admin_dsn(db: str = "postgres") -> str:
    """A libpq connection string for the Postgres server Odoo uses, as the role Odoo uses."""
    host = os.environ.get("PGHOST", "/tmp")
    user = os.environ.get("PGUSER", "odoo")
    return f"host={host} dbname={db} user={user}"


def _sql(statement: str, db: str = "postgres") -> None:
    """Runs one statement outside a transaction (CREATE and DROP DATABASE need that)."""
    with psycopg.connect(admin_dsn(db), autocommit=True) as conn:
        conn.execute(statement)


def _filestore(db: str) -> Path:
    """Where Odoo keeps a database's attachments on disk."""
    return HOME / "data" / "filestore" / db


def freeze(source: str = SOURCE_DB) -> None:
    """Copies the source company into the golden template. Run once after `edp.demo`.

    Every other connection to the source is closed first: Postgres can only copy a
    database nobody is connected to. The main Odoo server reconnects on its own.
    """
    _sql(f"DROP DATABASE IF EXISTS {GOLDEN} WITH (FORCE)")
    _sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
         f"WHERE datname = '{source}' AND pid <> pg_backend_pid()")
    _sql(f"CREATE DATABASE {GOLDEN} TEMPLATE {source}")
    if _filestore(source).exists():
        shutil.rmtree(_filestore(GOLDEN), ignore_errors=True)
        shutil.copytree(_filestore(source), _filestore(GOLDEN))


def server_up() -> bool:
    """Whether the episode server answers."""
    import urllib.request
    body = b'{"jsonrpc":"2.0","method":"call","params":{"service":"common","method":"version","args":[]}}'
    req = urllib.request.Request(f"http://localhost:{PORT}/jsonrpc", body, {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=2)
        return True
    except OSError:
        return False


def start_server(workers: int = 0) -> None:
    """Starts the episode-only Odoo server if it is not already running."""
    if server_up():
        return
    log = open(HOME / "voight-server.log", "a")
    args = [str(HOME / "venv/bin/odoo"), "-c", str(HOME / "odoo.conf"), "--http-port", str(PORT),
            f"--db-filter=^{PREFIX}", "--no-database-list", "--max-cron-threads=0"]
    if workers:
        args += ["--workers", str(workers)]
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    (HOME / "voight-server.pid").write_text(str(proc.pid))
    for _ in range(90):
        if server_up():
            return
        time.sleep(1)
    raise RuntimeError(f"episode server did not start; see {HOME / 'voight-server.log'}")


class Copy:
    """One episode's private database, reset from the template into a reusable slot."""

    def __init__(self, slot: int = 0) -> None:
        self.db = f"{PREFIX}slot{slot}"
        _sql(f"DROP DATABASE IF EXISTS {self.db} WITH (FORCE)")
        _sql(f"CREATE DATABASE {self.db} TEMPLATE {GOLDEN}")
        link = _filestore(self.db)
        if _filestore(GOLDEN).exists() and not link.exists():
            os.symlink(_filestore(GOLDEN), link)
        self.odoo = Odoo(f"http://localhost:{PORT}", self.db, USER, PASSWORD, retries=2, backoff=0.5)

    def conn(self) -> psycopg.Connection:
        """A direct database connection, used by the checker and never offered to the agent."""
        return psycopg.connect(admin_dsn(self.db), autocommit=True)

    def drop(self) -> None:
        """Removes the copy and its attachment link."""
        _sql(f"DROP DATABASE IF EXISTS {self.db} WITH (FORCE)")
        link = _filestore(self.db)
        if link.is_symlink():
            link.unlink()


@contextmanager
def fresh_copy(slot: int = 0):
    """A fresh copy for the length of a `with` block."""
    copy = Copy(slot)
    try:
        yield copy
    finally:
        copy.drop()
