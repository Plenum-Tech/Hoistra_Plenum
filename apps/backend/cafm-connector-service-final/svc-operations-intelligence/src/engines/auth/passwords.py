"""Hashing a password, and deciding whether one is good enough to accept.

bcrypt directly rather than through passlib: passlib 1.7.4 reads ``bcrypt.__about__`` to
detect the backend version, and bcrypt removed that attribute in 4.1. The pair installed
here is passlib 1.7.4 with bcrypt 5.0, which logs a backend-detection error and then
works — until it does not. The library underneath is the same one either way.

Note what is NOT here: no composition rules. Requiring an uppercase, a digit and a symbol
produces `Passw0rd!` and stops, which is both harder to remember and easier to guess than
four ordinary words. Length and a check against the obvious choices do more (NIST SP
800-63B §5.1.1.2 says as much).
"""
from __future__ import annotations

import hmac
import re
import unicodedata

import bcrypt

from ...config import settings

#: bcrypt truncates at 72 BYTES and silently ignores everything after. A passphrase whose
#: first 72 bytes match is therefore the same password — so anything longer is refused
#: rather than accepted-and-quietly-shortened, which would make a 200-character passphrase
#: no stronger than its opening sentence while telling the person it was accepted.
MAX_PASSWORD_BYTES = 72

#: Work factor. 12 is ~250ms on current server hardware: slow enough that an offline
#: attacker gets a few thousand guesses a second per core rather than billions, fast
#: enough that a login is not noticeably delayed.
BCRYPT_ROUNDS = 12

#: The passwords that get tried first, and the ones this platform's own name invites.
#: Not a substitute for a breach-corpus check (Have I Been Pwned's k-anonymity range API
#: is the real answer and needs an outbound call); this is the floor, not the ceiling.
_OBVIOUS = frozenset({
    "password", "passw0rd", "password1", "password123", "passwordpassword",
    "12345678", "123456789", "1234567890", "123456789012", "qwertyuiop",
    "letmein", "welcome", "welcome123", "iloveyou", "admin", "administrator",
    "changeme", "secret", "abc123", "monkey", "dragon", "sunshine", "princess",
    "football", "baseball", "trustno1", "master", "shadow", "superman",
    "hoistra", "hoistra123", "plenum", "plenumtech", "facilities", "maintenance",
})


class WeakPassword(ValueError):
    """The password was rejected. The message is safe to show the person choosing it."""


def _normalise(password: str) -> str:
    # NFKC so a password typed with a composed é and one typed with e + combining accent
    # are the same password. Without this, someone can set a password on one keyboard
    # layout and be unable to sign in from another.
    return unicodedata.normalize("NFKC", password or "")


def _squash(text: str) -> str:
    """Lowercased, stripped of anything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def validate(password: str, *, email: str = "", full_name: str = "") -> str:
    """Return the normalised password, or raise :class:`WeakPassword` saying why not.

    ``email`` and ``full_name`` are passed so the check can refuse a password that is
    simply the person's own address — the single most common choice a length rule alone
    lets through.
    """
    pw = _normalise(password)
    if not pw:
        raise WeakPassword("Enter a password.")

    minimum = int(settings.auth_password_min_length)
    if len(pw) < minimum:
        raise WeakPassword(f"Use at least {minimum} characters. Length is what makes a "
                           "password hard to guess; a memorable phrase beats a short "
                           "one with symbols in it.")

    encoded = pw.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise WeakPassword(
            f"That is longer than {MAX_PASSWORD_BYTES} bytes, which is the most bcrypt "
            "can read — anything past it would be ignored, so the password would be "
            "weaker than it looks. Shorten it."
        )
    if "\x00" in pw:
        # bcrypt treats NUL as a terminator: "a\0" + anything hashes as "a".
        raise WeakPassword("A password cannot contain a null character.")

    flat = _squash(pw)
    # Both the whole thing and its stem with trailing digits removed. An exact-match
    # denylist catches "password" and waves through "password2026" and "hoistra123456",
    # which is precisely the shape people reach for when told to add a number — so the
    # list would refuse only the passwords nobody was going to use anyway.
    stem = flat.rstrip("0123456789")
    if flat and (flat in _OBVIOUS or (stem and stem in _OBVIOUS)):
        raise WeakPassword("That password is one of the first things anyone would try. "
                           "Choose something else.")

    if len(set(pw)) <= 2:
        raise WeakPassword("That password repeats one or two characters. Its length is "
                           "not doing any work.")

    local = _squash((email or "").split("@", 1)[0])
    if local and len(local) >= 4 and local in flat:
        raise WeakPassword("Your password contains your email address. Anyone guessing "
                           "starts there.")

    for part in (full_name or "").split():
        squashed = _squash(part)
        if len(squashed) >= 4 and squashed in flat:
            raise WeakPassword("Your password contains your own name.")

    return pw


def hash_password(password: str) -> str:
    """A bcrypt digest, salt included, for ``users.password_hash``."""
    pw = _normalise(password).encode("utf-8")
    if len(pw) > MAX_PASSWORD_BYTES:
        # Never silently truncate: the caller would be told a long passphrase was stored
        # when only its first 72 bytes were.
        raise WeakPassword(f"Password exceeds {MAX_PASSWORD_BYTES} bytes.")
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Whether the password matches. False for anything malformed — never an exception."""
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(
            _normalise(password).encode("utf-8")[:MAX_PASSWORD_BYTES],
            stored_hash.encode("utf-8"),
        )
    except (ValueError, TypeError):
        # A hash this code cannot read — a seed placeholder, an argon2 digest from some
        # other system, a truncated column. It is not a match, and it is not a crash.
        return False


def burn_time() -> None:
    """Hash a throwaway value so a missing account costs the same as a wrong password.

    Without this, "no such user" returns in a millisecond and "wrong password" takes 250,
    and the difference is a reliable, remotely measurable answer to "does this person
    have an account here" — which is the thing the generic error message exists to hide.
    """
    bcrypt.checkpw(
        b"timing-equalisation",
        bcrypt.hashpw(b"timing-equalisation", bcrypt.gensalt(rounds=BCRYPT_ROUNDS)),
    )


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))
