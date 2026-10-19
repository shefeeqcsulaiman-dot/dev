"""Super Admin > Restore from backup: read a company's SQL backup back into a NEW company.

The file is the one iter_company_sql_dump() (routers/app_data.py) writes -- Download Backup,
Super Admin's per-company backup, or a nightly platform-backups/<date>/<company>.sql.gz. It
is never executed: the parser below accepts only that dump's own statements

    INSERT INTO <known table> (<known columns>) VALUES (<literals>) ON CONFLICT (id) DO NOTHING;

(plus comments, BEGIN; and COMMIT;), reads the literals as data and writes the rows itself.

Every UUID in the file -- row ids, foreign keys, ids inside the JSON payloads -- is replaced
by uuid5(<a namespace new for this restore>, uuid), so the copy shares no id with the
original (or with an earlier restore of the same file) and its references stay consistent.
A foreign key to something the backup doesn't hold (users, roles, warehouses...) is cleared.
The original company is never read from or written to, apart from copying its profile
(name, modules, currency...) when it still exists.

ESS and branch logins are not copied: portal usernames are unique platform-wide, and the
copy's employees get a random password so nobody can sign in to it with the employee
number. The ledger (journals, GL, source transactions, tax lines) comes from the backup as
is; nothing is re-posted. Older backups have no tax lines: those are rebuilt from the
restored source transactions, the same way posting creates them (ensure_tax_line()).

Runs as a background job (same job store as app/demo_data.py), one at a time platform-wide,
in a single transaction: if anything fails, nothing of the new company is left behind.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Iterator

from app import cache

MAX_UPLOAD_BYTES = 200 * 1024 * 1024        # the file as uploaded (.sql.gz or .sql)
MAX_TEXT_CHARS = 1024 * 1024 * 1024         # the SQL text once decompressed
BATCH = 500
PAUSE = 0.05                                 # seconds between batches: leave room for everyone else

_JOB_KEY = "sa:restore:{id}"
_LOCK_KEY = "sa:restore:lock"
_LOCK_TTL = 3 * 3600
_jobs: dict[str, dict] = {}
_local_lock = threading.Lock()

# Tables a backup may hold, in the order foreign keys need. The first group is small and
# is read into memory on the first pass, then written before everything else.
EARLY_TABLES = ("branches", "accounts", "tax_codes", "employees")
TABLES = (
    "branches", "accounts", "tax_codes", "employees", "app_data_records", "invoices", "invoice_lines",
    "journal_entries", "journal_lines", "stock_product_mappings", "stock_movements", "period_locks",
    "audit_logs", "source_transactions", "source_transaction_lines", "tax_lines", "general_ledger_entries",
    "payroll_runs", "payroll_items", "leave_requests", "attendance_details",
)
# Never copied: logins are unique platform-wide, and the copy must not be signed in to.
_LOGIN_FIELDS = {"branches": ("username", "password_hash"), "employees": ("username", "password_hash")}
_EMPLOYEE_SECRET_KEYS = ("password", "password_hash", "ess_password", "new_password")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class BackupError(ValueError):
    """The file isn't a TaxFlow backup this code can read (message says where)."""


# ── reading the file ───────────────────────────────────────────────────────────

def open_text(path: str):
    """The backup as text, gunzipped when it starts with the gzip magic bytes."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "r", encoding="utf-8", newline="")


_E_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}
_E_SEQ = re.compile(r"\\(.)|''", re.S)


def _unescape_e(body: str) -> str:
    return _E_SEQ.sub(lambda m: "'" if m.group(1) is None else _E_ESCAPES.get(m.group(1), m.group(1)), body)


# One regex per part of a statement (far faster than token by token: a backup holds
# millions of values). Every pattern needs its closing character, so text cut off at the
# end of the buffer never matches: the reader loads more and tries the statement again.
_WS = re.compile(r"\s+")
_COMMENT = re.compile(r"--([^\n]*)\n")
_BEGIN_COMMIT = re.compile(r"(?:BEGIN|COMMIT)\s*;", re.I)
_INSERT = re.compile(r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([A-Za-z0-9_,\s]*)\)\s*VALUES\s*\(", re.I)
_COLUMN = re.compile(r"[a-z_][a-z0-9_]*")
_VALUE = re.compile(r"""\s*(?:
      (?P<word>NULL|TRUE|FALSE)
    | (?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
    | [Ee]'(?P<e>[^'\\]*(?:(?:\\.|'')[^'\\]*)*)'
    | '(?P<s>[^']*(?:''[^']*)*)'
    )\s*(?P<sep>[,)])""", re.X | re.S | re.I)
_TAIL = re.compile(r"\s*ON\s+CONFLICT\s*\(\s*id\s*\)\s*DO\s+NOTHING\s*;", re.I)
_WORDS = {"NULL": None, "TRUE": True, "FALSE": False}
MAX_VALUE_CHARS = 64 * 1024 * 1024


class _Incomplete(Exception):
    """The statement runs past the text read so far."""


class _Reader:
    """The SQL text, read in chunks; `line` is the line number at the start of `buf`."""

    CHUNK = 4 << 20

    def __init__(self, fh):
        self.fh = fh
        self.buf = ""
        self.pos = 0
        self.eof = False
        self.line = 1
        self.read_chars = 0

    def more(self, size: int) -> None:
        data = self.fh.read(size)
        if not data:
            self.eof = True
            return
        self.read_chars += len(data)
        if self.read_chars > MAX_TEXT_CHARS:
            raise BackupError("The backup is larger than 1 GB once unpacked")
        self.line += self.buf.count("\n", 0, self.pos)
        self.buf = self.buf[self.pos:] + data
        self.pos = 0

    def line_at(self, pos: int) -> int:
        # Counted on from the last call: re-counting a 4 MB buffer for every statement
        # was nearly all of the parse time.
        mark = getattr(self, "_mark", None)
        if mark is None or mark[2] is not self.buf or mark[0] > pos:
            mark = (0, self.line, self.buf)
        line = mark[1] + self.buf.count("\n", mark[0], pos)
        self._mark = (pos, line, self.buf)
        return line


def _parse_insert(buf: str, pos: int, eof: bool, line: int):
    """(table, columns, values, end) of the INSERT at `pos`. _Incomplete when it may go on
    past the buffer, BackupError when it can't be one of the backup's own INSERTs."""
    def bad(at: int, what: str):
        if not eof and len(buf) - at < MAX_VALUE_CHARS:
            raise _Incomplete
        raise BackupError(f"Line {line}: {what}")

    m = _INSERT.match(buf, pos)
    if m is None:
        bad(pos, "only INSERT statements are allowed in a backup")
    cols = [c.strip().lower() for c in m.group(2).split(",")]
    if not all(_COLUMN.fullmatch(c) for c in cols):
        raise BackupError(f"Line {line}: bad column list")
    values: list[Any] = []
    at = m.end()
    while True:
        v = _VALUE.match(buf, at)
        if v is None:
            bad(at, "only plain values are allowed (text, numbers, NULL, TRUE, FALSE)")
        if v.group("word") is not None:
            values.append(_WORDS[v.group("word").upper()])
        elif v.group("num") is not None:
            values.append(v.group("num"))
        elif v.group("e") is not None:
            values.append(_unescape_e(v.group("e")))
        else:
            values.append(v.group("s").replace("''", "'"))
        at = v.end()
        if v.group("sep") == ")":
            break
    t = _TAIL.match(buf, at)
    if t is None:
        bad(at, "an INSERT must end with ON CONFLICT (id) DO NOTHING;")
    if len(values) != len(cols):
        raise BackupError(f"Line {line}: {len(cols)} columns but {len(values)} values")
    return m.group(1).lower(), cols, values, t.end()


def statements(fh) -> Iterator[tuple[str, list[str], list[Any], int] | tuple[str, str]]:
    """("comment", text) or (table, columns, values, line) for each INSERT. Anything else is
    refused: this reads a backup, it doesn't run SQL."""
    r = _Reader(fh)
    size = r.CHUNK
    while True:
        ws = _WS.match(r.buf, r.pos)
        if ws:
            r.pos = ws.end()
        if len(r.buf) - r.pos < 16 and not r.eof:  # room to recognise the next statement
            r.more(size)
            continue
        if r.pos >= len(r.buf):
            return
        line = r.line_at(r.pos)
        try:
            if r.buf.startswith("--", r.pos):
                c = _COMMENT.match(r.buf, r.pos)
                if c is None:
                    if not r.eof:
                        raise _Incomplete
                    text, end = r.buf[r.pos + 2:], len(r.buf)
                else:
                    text, end = c.group(1), c.end()
                r.pos = end
                yield ("comment", "--" + text)
                continue
            bc = _BEGIN_COMMIT.match(r.buf, r.pos)
            if bc:
                r.pos = bc.end()
                continue
            if r.buf[r.pos:r.pos + 6].upper() != "INSERT":
                word = re.match(r"\S{1,30}", r.buf[r.pos:r.pos + 30])
                raise BackupError(f"Line {line}: only INSERT statements are allowed in a backup, found {word.group(0)!r}")
            table, cols, values, end = _parse_insert(r.buf, r.pos, r.eof, line)
            r.pos = end
            size = r.CHUNK
            yield (table, cols, values, line)
        except _Incomplete:
            r.more(size)  # load more and try this statement again
            size = min(size * 2, 64 << 20)


# ── table metadata ─────────────────────────────────────────────────────────────

def _models() -> dict[str, Any]:
    from app.models import Base

    out = {}
    for mapper in Base.registry.mappers:
        table = getattr(mapper.class_, "__table__", None)
        if table is not None and table.name in TABLES:
            out[table.name] = mapper.class_
    return out


class _Table:
    def __init__(self, model: Any):
        self.model = model
        self.table = model.__table__
        self.name = self.table.name
        mapper = model.__mapper__
        self.attr = {}  # column name -> ORM attribute key
        for prop in mapper.column_attrs:
            for col in prop.columns:
                if getattr(col, "table", None) is self.table:
                    self.attr[col.name] = prop.key
        self.cols = {c.name: c for c in self.table.columns}
        self.fk = {}  # column name -> target table name
        for c in self.table.columns:
            for fk in c.foreign_keys:
                self.fk[c.name] = fk.column.table.name

    def check(self, cols: list[str], values: list[Any], line: int) -> None:
        for c, v in zip(cols, values):
            # Older backups name a few columns the table never had (accounts.parent_id,
            # invoices.customer_trn), always empty: skipped. One holding data is refused.
            if c not in self.cols and v is not None:
                raise BackupError(f"Line {line}: table {self.name} has no column {c!r}")
        if "id" not in cols:
            raise BackupError(f"Line {line}: {self.name} row without an id")


def _coerce(col: Any, value: Any) -> Any:
    if value is None:
        return None
    try:
        py = col.type.python_type
    except NotImplementedError:
        py = str
    if py is bool:
        return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "t", "1", "yes")
    if isinstance(value, bool):
        value = "1" if value else "0"
    text = str(value)
    if py is int:
        return int(Decimal(text))
    if py is Decimal:
        return Decimal(text)
    if py is float:
        return float(text)
    if py is datetime:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if py is date:
        return date.fromisoformat(text.strip()[:10])
    return text


# ── job state (shared with every server copy through Redis, like demo_data) ────

def _save_job(job: dict) -> None:
    job["updated_at"] = datetime.now(UTC).isoformat()
    r = cache._redis()
    if r is not None:
        try:
            r.set(_JOB_KEY.format(id=job["id"]), json.dumps(job, default=str), ex=7 * 86400)
            return
        except Exception:
            pass
    _jobs[job["id"]] = dict(job)


def get_job(job_id: str) -> dict | None:
    r = cache._redis()
    if r is not None:
        try:
            raw = r.get(_JOB_KEY.format(id=job_id))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return _jobs.get(job_id)


def _take_lock(job_id: str) -> bool:
    r = cache._redis()
    if r is not None:
        try:
            return bool(r.set(_LOCK_KEY, job_id, nx=True, ex=_LOCK_TTL))
        except Exception:
            pass
    return _local_lock.acquire(blocking=False)


def _release_lock(job_id: str) -> None:
    r = cache._redis()
    if r is not None:
        try:
            if r.get(_LOCK_KEY) == job_id:
                r.delete(_LOCK_KEY)
            return
        except Exception:
            pass
    if _local_lock.locked():
        _local_lock.release()


def restore_running() -> bool:
    r = cache._redis()
    if r is not None:
        try:
            return bool(r.exists(_LOCK_KEY))
        except Exception:
            pass
    return _local_lock.locked()


def start(path: str, admin_email: str, admin_password: str, name: str, started_by: str) -> dict:
    """Start restoring the backup at `path` (deleted when done) into a new company."""
    job = {"id": uuid.uuid4().hex[:12], "status": "running", "step": "Checking the file", "progress": 0,
           "counts": {}, "skipped": {}, "notes": [], "company_id": None, "company_name": None,
           "started_at": datetime.now(UTC).isoformat(), "started_by": started_by}
    if not _take_lock(job["id"]):
        os.unlink(path)
        raise RuntimeError("Another restore is running; try again when it has finished")
    _save_job(job)
    _spawn(_run, (job, path, admin_email, admin_password, name))
    return job


def _spawn(target, args) -> None:
    threading.Thread(target=target, args=args, name="backup-restore", daemon=True).start()


# ── the restore ────────────────────────────────────────────────────────────────

class _Restore:
    def __init__(self, db: Any, job: dict, path: str):
        self.db = db
        self.job = job
        self.path = path
        self.models = _models()
        self.tables = {name: _Table(model) for name, model in self.models.items()}
        self.namespace = uuid.uuid4()
        self.source_company: str | None = None
        self.ids: dict[str, set[str]] = {}       # table -> old ids in the file
        self.early: dict[str, list[tuple[list[str], list[Any]]]] = {t: [] for t in EARLY_TABLES}
        self.total = 0
        self.done = 0
        self.saved_at = 0.0
        self.locked_hash = ""
        self.principal = None
        self.company_id = ""

    def new_id(self, old: str) -> str:
        return str(uuid.uuid5(self.namespace, old.lower()))

    def remap(self, text: str) -> str:
        return _UUID.sub(lambda m: self.new_id(m.group(0)), text) if "-" in text else text

    # first pass: check every statement, note ids, keep the small tables
    def scan(self) -> None:
        header_company = None
        with open_text(self.path) as fh:
            first = fh.read(200)
            if "TaxFlow Database Backup" not in first:
                raise BackupError("Not a TaxFlow backup: the file doesn't start with the TaxFlow backup header")
            fh.seek(0)
            for st in statements(fh):
                if st[0] == "comment":
                    m = re.match(r"--\s*Company ID\s*:\s*(\S+)", st[1])
                    if m:
                        header_company = m.group(1)
                    continue
                table, cols, values, line = st
                spec = self.tables.get(table)
                if spec is None:
                    raise BackupError(f"Line {line}: table {table!r} isn't part of a TaxFlow backup")
                spec.check(cols, values, line)
                row = dict(zip(cols, values))
                if not isinstance(row["id"], str) or not _UUID.fullmatch(row["id"]):
                    raise BackupError(f"Line {line}: {table} row with a bad id")
                self.ids.setdefault(table, set()).add(row["id"])
                if header_company is None and isinstance(row.get("company_id"), str):
                    header_company = row["company_id"]
                if table in self.early:
                    self.early[table].append((cols, values))
                self.total += 1
        if not self.total:
            raise BackupError("The backup has no rows to restore")
        if not header_company or not _UUID.fullmatch(header_company):
            raise BackupError("The backup doesn't say which company it is from")
        self.source_company = header_company

    # one row of the file -> attribute dict for the new company (None = skip it)
    def row(self, table: str, cols: list[str], values: list[Any]) -> dict[str, Any] | None:
        spec = self.tables[table]
        out: dict[str, Any] = {}
        for name, value in zip(cols, values):
            col = spec.cols.get(name)
            if col is None:
                continue  # a column the table doesn't have, always empty (_Table.check)
            target = spec.fk.get(name)
            if target is not None and value is not None:
                if target == "companies":
                    if value != self.source_company:
                        return None  # another company's row: never copied
                elif target not in self.ids or str(value) not in self.ids[target]:
                    if not col.nullable:
                        return None  # points at something the backup doesn't hold
                    value = None
            if isinstance(value, str):
                value = self.remap(value)
            out[spec.attr[name]] = _coerce(col, value)
        for field in _LOGIN_FIELDS.get(table, ()):
            if field in spec.cols:
                out[spec.attr[field]] = None
        if table == "employees":
            out["password_hash"] = self.locked_hash
        return out

    def progress(self, step: str | None = None, force: bool = False) -> None:
        if step:
            self.job["step"] = step
        self.job["progress"] = min(99, int(self.done * 100 / max(self.total, 1)))
        now = time.monotonic()
        if force or now - self.saved_at > 1.5:
            self.saved_at = now
            _save_job(self.job)

    def count(self, table: str, n: int = 1, skipped: bool = False) -> None:
        bucket = self.job["skipped" if skipped else "counts"]
        bucket[table] = bucket.get(table, 0) + n

    def insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        from sqlalchemy import insert

        if rows:
            self.db.execute(insert(self.models[table]), rows)
            self.count(table, len(rows))
            time.sleep(PAUSE)

    def write_early(self) -> None:
        for table in EARLY_TABLES:
            rows = []
            for cols, values in self.early[table]:
                self.done += 1
                r = self.row(table, cols, values)
                if r is None:
                    self.count(table, skipped=True)
                else:
                    rows.append(r)
            if table == "accounts":
                rows = _parents_first(rows)
            self.progress(f"Restoring {table.replace('_', ' ')}")
            for start in range(0, len(rows), BATCH):
                self.insert(table, rows[start:start + BATCH])
            self.db.flush()
        self.early = {}

    def write_rest(self) -> None:
        pending: list[dict[str, Any]] = []
        current = None
        with open_text(self.path) as fh:
            for st in statements(fh):
                if st[0] == "comment":
                    continue
                table, cols, values, _line = st
                if table in EARLY_TABLES:
                    continue
                self.done += 1
                if table != current:
                    self._flush(current, pending)
                    pending = []
                    current = table
                    self.progress(f"Restoring {table.replace('_', ' ')}")
                r = self.row(table, cols, values)
                if r is None:
                    self.count(table, skipped=True)
                    continue
                pending.append(r)
                if len(pending) >= BATCH:
                    self._flush(table, pending)
                    pending = []
                    self.progress()
        self._flush(current, pending)

    def _flush(self, table: str | None, rows: list[dict[str, Any]]) -> None:
        if not table or not rows:
            return
        if table == "app_data_records":
            self._app_records(rows)
        else:
            self.insert(table, rows)
        self.db.flush()

    def _app_records(self, rows: list[dict[str, Any]]) -> None:
        """Through the ORM, so the typed tables (customers, products, rota, employees...)
        are rebuilt by their mirrors exactly as on a normal save."""
        from fastapi import HTTPException

        from app.employee_records import store_main_copy
        from app.models import AppDataRecord
        from app.routers.app_data import sync_domain_model

        branches = {self.new_id(b) for b in self.ids.get("branches", ())}
        objs = []
        for r in rows:
            payload = r.get("_payload") or ""
            try:
                doc = json.loads(payload) if payload else None
            except ValueError:
                doc = None
            if isinstance(doc, dict):
                if r.get("collection") == "employees":
                    for key in _EMPLOYEE_SECRET_KEYS:
                        doc.pop(key, None)
                branch = str(doc.get("branch_id") or "").strip()
                if branch and branch not in branches:
                    doc.pop("branch_id", None)
                    branch = ""
                r["branch_id"] = branch or None
                payload = json.dumps(doc, ensure_ascii=False, default=str)
            obj = AppDataRecord(id=r["id"], company_id=self.company_id, collection=r.get("collection") or "app_actions",
                                record_key=r.get("record_key"), payload=payload, branch_id=r.get("branch_id"))
            if r.get("created_at"):
                obj.created_at = r["created_at"]
            if r.get("updated_at"):
                obj.updated_at = r["updated_at"]
            objs.append((obj, doc))
        self.db.add_all([o for o, _ in objs])
        self.db.flush()
        for obj, doc in objs:
            if obj.collection == "employees" and isinstance(doc, dict):
                # The employees row holds what the backup's employees table didn't (allowances,
                # IBAN, branch, photo, the record's other fields): fill it from the record.
                try:
                    with self.db.begin_nested():
                        sync_domain_model(self.db, self.principal, "employees", doc)
                        self.db.flush()
                        store_main_copy(self.db, self.company_id, obj)
                except HTTPException as exc:
                    self.job["notes"].append(f"Employee {obj.record_key}: {exc.detail}")
        self.count("app_data_records", len(objs))
        time.sleep(PAUSE)

    def finish_ledger(self) -> None:
        """Add what posting needs that a backup doesn't hold (voucher types; tax codes for
        an older backup -- existing codes are kept), and the VAT lines an older backup lacks.
        Nothing else is seeded: the copy's lists stay exactly as backed up."""
        from app.accounting_posting import ensure_tax_line
        from app.company_defaults import seed_tax_codes, seed_voucher_types
        from app.models import SourceTransaction

        self.progress("Finishing the ledger", force=True)
        self.db.flush()
        seed_voucher_types(self.db, self.company_id)
        seed_tax_codes(self.db, self.company_id)
        self.db.flush()
        if not self.ids.get("tax_lines") and self.ids.get("source_transactions"):
            n = 0
            for st in self.db.query(SourceTransaction).filter(SourceTransaction.company_id == self.company_id):
                ensure_tax_line(self.db, st)
                n += 1
                if n % BATCH == 0:
                    self.db.flush()
            self.db.flush()
            self.job["notes"].append("VAT lines rebuilt from the restored transactions (older backup without them)")


def _parents_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {r["id"]: r for r in rows}
    out, seen = [], set()

    def visit(r: dict[str, Any], depth: int = 0) -> None:
        if r["id"] in seen:
            return
        parent = by_id.get(r.get("parent_account_id"))
        if parent is not None and depth < 50:
            visit(parent, depth + 1)
        seen.add(r["id"])
        out.append(r)

    for r in rows:
        visit(r)
    return out


def _run(job: dict, path: str, admin_email: str, admin_password: str, name: str) -> None:
    from sqlalchemy import text

    from app.auth_principal import Principal
    from app.database import SessionLocal
    from app.models import Company, User
    from app.module_catalog import ALL_MODULES
    from app.security import hash_password

    db = SessionLocal()
    rs = _Restore(db, job, path)
    try:
        rs.scan()
        _save_job(job)
        if db.get_bind().dialect.name == "postgresql":
            # The platform-wide guard if the Redis lock was unavailable.
            if not db.execute(text("SELECT pg_try_advisory_xact_lock(724110)")).scalar():
                raise RuntimeError("Another restore is running; try again when it has finished")
        if db.query(User).filter(User.email == admin_email).first():
            raise BackupError("That admin email is already registered")
        source = db.get(Company, rs.source_company)
        today = date.today().isoformat()
        company = Company(id=rs.new_id(rs.source_company))
        if source is not None:
            # trn is unique per company: the copy starts without one (set it in Edit Company).
            skip = {"id", "created_at", "updated_at", "fta_username", "name", "trn"}
            for prop in Company.__mapper__.column_attrs:
                if prop.key not in skip:
                    setattr(company, prop.key, getattr(source, prop.key))
            base = source.name
        else:
            company.modules_enabled = json.dumps(ALL_MODULES)
            base = "Restored company"
        company.name = (name or "").strip()[:255] or f"{base} (restored {today})"
        db.add(company)
        db.flush()
        admin = User(company_id=company.id, email=admin_email, full_name=f"{company.name} Admin"[:255],
                     password_hash=hash_password(admin_password), role="admin")
        db.add(admin)
        db.flush()
        rs.company_id = company.id
        rs.locked_hash = hash_password(secrets.token_urlsafe(32))
        rs.principal = Principal(kind="user", company_id=company.id, display_name="Backup restore",
                                 is_admin=True, user=admin)
        job["company_id"], job["company_name"] = company.id, company.name
        rs.write_early()
        rs.write_rest()
        rs.finish_ledger()
        from app.routers.superadmin import _write_audit_blob

        total = sum(job["counts"].values())
        _write_audit_blob(db, company.id, job.get("started_by") or "superadmin", "restore_backup",
                          f"from company {rs.source_company}", f"Done: {total} rows")
        db.commit()
        job.update(status="done", step="Done", progress=100)
    except BackupError as exc:
        db.rollback()
        job.update(status="error", step="Failed", error=str(exc), company_id=None)
    except Exception as exc:  # noqa: BLE001 - reported to the Super Admin, then logged
        db.rollback()
        import logging

        logging.getLogger(__name__).exception("backup restore failed")
        # The database's own message (not the SQL it was running), first line only.
        detail = str(getattr(exc, "orig", None) or exc).split("\n")[0][:300]
        job.update(status="error", step="Failed", error=f"Restore failed, nothing was created: {detail}",
                   company_id=None)
    finally:
        db.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        job["finished_at"] = datetime.now(UTC).isoformat()
        _save_job(job)
        _release_lock(job["id"])
