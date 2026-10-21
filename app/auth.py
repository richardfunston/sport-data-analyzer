"""Authentication: password hashing, and the signed-cookie session.

Kept separate from :mod:`app.storage` so the store can hash a password without
owning the auth concern, and so the session machinery has one home. Nothing here
imports the store at module level - it reaches the store through the app, which
keeps the import graph one-way.
"""

from __future__ import annotations

from werkzeug.security import check_password_hash, generate_password_hash

__all__ = ["hash_password", "check_password"]


def hash_password(password: str) -> str:
    """Hash a password for storage.

    ``werkzeug`` uses scrypt with a per-password salt. The plaintext is never
    written anywhere.
    """
    return generate_password_hash(password)


def check_password(password: str, password_hash: str) -> bool:
    """True when ``password`` matches the stored hash."""
    return check_password_hash(password_hash, password)
