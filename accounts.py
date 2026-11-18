"""User accounts and sessions.

Reset a forgotten password from the container console:
    python accounts.py mot-de-passe NOM NOUVEAU_MOT_DE_PASSE
"""
import contextlib
import hashlib
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from pathlib import Path

NAME = re.compile(r"^[\w .'-]{2,30}$")
MIN_PASSWORD = 10
MAX_PASSWORD = 200
SESSION_SECONDS = 30 * 86400
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "maxmem": 2 ** 26}
LOGIN_REFUSED = "Nom ou mot de passe incorrect"
SEEN_EVERY = 20  # seconds between two updates of a last visit: the page asks every 15 s
ONLINE_SECONDS = 5 * 60  # a visit this recent counts as online


class AccountError(Exception):
    """The message is shown to the user."""


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


def check_password_rules(password):
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AccountError(f"Le mot de passe doit faire au moins {MIN_PASSWORD} caractères")


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Accounts:
    def __init__(self, connect):
        self.connect = connect
        self.failures = {}
        self.lock = threading.Lock()
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, "
                "name TEXT NOT NULL UNIQUE COLLATE NOCASE, salt BLOB NOT NULL, "
                "password BLOB NOT NULL, created INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, "
                "user_id INTEGER NOT NULL, expires INTEGER NOT NULL)"
            )
            # Columns added after the first installs.
            if "invited_by" not in self.columns(db, "users"):
                db.execute("ALTER TABLE users ADD COLUMN invited_by INTEGER")
            if "is_admin" not in self.columns(db, "users"):
                db.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            if "last_seen" not in self.columns(db, "sessions"):
                db.execute("ALTER TABLE sessions ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0")
            # Kept on the account too, so that a logout does not erase the last visit.
            if "last_seen" not in self.columns(db, "users"):
                db.execute("ALTER TABLE users ADD COLUMN last_seen INTEGER")

    @staticmethod
    def columns(db, table):
        return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]

    def count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def register(self, name, password, invited_by=None):
        name = name.strip()
        if not NAME.match(name):
            raise AccountError("Le nom doit faire de 2 à 30 caractères, sans symbole")
        check_password_rules(password)
        salt = secrets.token_bytes(16)
        hashed = hash_password(password, salt)
        try:
            with self.connect() as db:
                cursor = db.execute(
                    "INSERT INTO users (name, salt, password, created, invited_by) VALUES (?, ?, ?, ?, ?)",
                    (name, salt, hashed, int(time.time()), invited_by),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise AccountError("Ce nom est déjà pris") from None

    def is_locked(self, key):
        with self.lock:
            count, until = self.failures.get(key, (0, 0))
            return count >= MAX_FAILURES and time.time() < until

    def note_failure(self, key):
        with self.lock:
            count, until = self.failures.get(key, (0, 0))
            count = 1 if time.time() >= until else count + 1
            self.failures[key] = (count, time.time() + LOCK_SECONDS)

    def login(self, name, password):
        key = name.strip().lower()
        if self.is_locked(key):
            raise AccountError("Trop d'essais : réessayez dans 15 minutes")
        with self.connect() as db:
            row = db.execute("SELECT id, salt, password FROM users WHERE name = ?", (name.strip(),)).fetchone()
        # Hash even for an unknown name, so that the delay reveals nothing.
        salt = row["salt"] if row else b"0" * 16
        hashed = hash_password(password[:MAX_PASSWORD], salt)
        if not row or not secrets.compare_digest(hashed, row["password"]):
            self.note_failure(key)
            raise AccountError(LOGIN_REFUSED)
        with self.lock:
            self.failures.pop(key, None)
        return row["id"]

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            db.execute(
                "INSERT INTO sessions (token, user_id, expires, last_seen) VALUES (?, ?, ?, ?)",
                (token_hash(token), user_id, now + SESSION_SECONDS, now),
            )
            db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, user_id))
        return token

    def session_user(self, token):
        now = int(time.time())
        with self.connect() as db:
            row = db.execute(
                "SELECT users.id, users.name, sessions.last_seen FROM sessions "
                "JOIN users ON users.id = sessions.user_id WHERE sessions.token = ? AND sessions.expires > ?",
                (token_hash(token), now),
            ).fetchone()
            if row and now - row["last_seen"] >= SEEN_EVERY:  # a few writes a minute at most
                db.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (now, token_hash(token)))
                db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, row["id"]))
        return {"id": row["id"], "name": row["name"]} if row else None

    def online_count(self):
        """How many people used the site in the last few minutes."""
        now = int(time.time())
        with self.connect() as db:
            return db.execute(
                "SELECT COUNT(DISTINCT user_id) FROM sessions WHERE last_seen >= ? AND expires > ?",
                (now - ONLINE_SECONDS, now),
            ).fetchone()[0]

    def end_session(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token = ?", (token_hash(token),))

    def admin_id(self):
        """The first account created owns the site: it can name other administrators."""
        with self.connect() as db:
            return db.execute("SELECT MIN(id) FROM users").fetchone()[0]

    def is_admin(self, user_id):
        if user_id is None:
            return False
        with self.connect() as db:
            row = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row) and (bool(row["is_admin"]) or user_id == self.admin_id())

    def set_admin(self, user_id, is_admin):
        if user_id == self.admin_id():
            raise AccountError("Le propriétaire du site reste administrateur")
        with self.connect() as db:
            changed = db.execute(
                "UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, user_id)
            ).rowcount
        if not changed:
            raise AccountError("Utilisateur inconnu")

    def list_users(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT users.id, users.name, users.created, users.invited_by, users.is_admin, "
                "MAX(sessions.expires) AS session_end, MAX(sessions.last_seen) AS seen, "
                "MAX(COALESCE(users.last_seen, 0), COALESCE(MAX(sessions.last_seen), 0)) AS last_seen "
                "FROM users LEFT JOIN sessions ON sessions.user_id = users.id "
                "GROUP BY users.id ORDER BY users.id"
            ).fetchall()
        now = int(time.time())
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "created": row["created"],
                "invited_by": row["invited_by"],
                "is_admin": bool(row["is_admin"]),
                "online": bool(row["seen"]) and row["seen"] >= now - ONLINE_SECONDS,
                "last_seen": row["last_seen"] or None,  # still known after a logout
                # A session lasts a fixed time, so its end tells when it started.
                "last_login": row["session_end"] - SESSION_SECONDS if row["session_end"] else None,
            }
            for row in rows
        ]

    def delete_user(self, user_id):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def temporary_password(self, user_id):
        """Set a random password for an account and return it."""
        with self.connect() as db:
            row = db.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise AccountError("Utilisateur inconnu")
        password = secrets.token_urlsafe(12)
        self.reset_password(row["name"], password)
        return password

    def change_password(self, user, current, new, kept_token=""):
        """Change the password and close the account's other sessions, except kept_token."""
        check_password_rules(new)
        self.login(user["name"], current)
        salt = secrets.token_bytes(16)
        with self.connect() as db:
            db.execute(
                "UPDATE users SET salt = ?, password = ? WHERE id = ?",
                (salt, hash_password(new, salt), user["id"]),
            )
            db.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token != ?", (user["id"], token_hash(kept_token)),
            )

    def reset_password(self, name, password):
        check_password_rules(password)
        salt = secrets.token_bytes(16)
        with self.connect() as db:
            row = db.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
            if not row:
                raise AccountError("Utilisateur inconnu")
            db.execute(
                "UPDATE users SET salt = ?, password = ? WHERE id = ?",
                (salt, hash_password(password, salt), row["id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))


def main():
    if len(sys.argv) != 4 or sys.argv[1] != "mot-de-passe":
        raise SystemExit("Usage : python accounts.py mot-de-passe NOM NOUVEAU_MOT_DE_PASSE")
    path = Path(os.environ.get("METEO_DATA") or "data") / "meteo.db"

    @contextlib.contextmanager
    def connect():
        db = sqlite3.connect(path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    try:
        Accounts(connect).reset_password(sys.argv[2], sys.argv[3])
    except AccountError as err:
        raise SystemExit(str(err)) from None
    print("Mot de passe changé. Les sessions de cet utilisateur sont fermées.")


if __name__ == "__main__":
    main()
