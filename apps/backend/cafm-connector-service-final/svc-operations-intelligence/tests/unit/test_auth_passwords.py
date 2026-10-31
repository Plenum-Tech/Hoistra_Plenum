"""What a password has to be, and what hashing one must never quietly do."""
from __future__ import annotations

import time

import pytest

from src.engines.auth import passwords as P


# ── the policy ───────────────────────────────────────────────────────────────


def test_a_long_ordinary_phrase_is_accepted():
    assert P.validate("correct horse battery staple") == "correct horse battery staple"


def test_short_is_refused_with_a_reason_a_person_can_act_on():
    with pytest.raises(P.WeakPassword) as exc:
        P.validate("Sh0rt!")
    assert "at least" in str(exc.value)


def test_the_obvious_choices_are_refused():
    for pw in ("passwordpassword", "123456789012", "Password123!!", "hoistra123456"):
        with pytest.raises(P.WeakPassword):
            P.validate(pw)


def test_a_long_password_made_of_two_characters_is_refused():
    """Length alone is not the test. 'ababababababababab' passes a length rule and has
    almost no search space."""
    with pytest.raises(P.WeakPassword) as exc:
        P.validate("ababababababababab")
    assert "repeats" in str(exc.value)


def test_your_own_email_is_not_a_password():
    with pytest.raises(P.WeakPassword) as exc:
        P.validate("balaperera2026!!", email="balaperera@example.com")
    assert "email" in str(exc.value)


def test_your_own_name_is_not_a_password():
    with pytest.raises(P.WeakPassword) as exc:
        P.validate("bala-perera-1985", email="x@y.com", full_name="Bala Perera")
    assert "name" in str(exc.value)


def test_a_short_name_fragment_does_not_block_an_unrelated_password():
    """A three-letter name must not veto every password containing those letters —
    otherwise someone called Bo cannot use a passphrase with 'bo' anywhere in it."""
    assert P.validate("thunderous-cobalt-ridge", email="x@y.com", full_name="Bo Li")


def test_composition_rules_are_not_imposed():
    """No 'must contain a digit and a symbol'. Those rules produce Passw0rd! and stop."""
    assert P.validate("all lower case words here")


# ── the 72-byte cliff ────────────────────────────────────────────────────────


def test_a_password_past_bcrypts_limit_is_refused_not_silently_shortened():
    """bcrypt reads 72 bytes and ignores the rest. Accepting a 200-character passphrase
    would store something no stronger than its opening sentence, while telling the person
    it was accepted — and any other passphrase sharing those 72 bytes would sign them in.
    """
    long_one = "a-very-long-passphrase-" * 10
    assert len(long_one.encode()) > P.MAX_PASSWORD_BYTES
    with pytest.raises(P.WeakPassword) as exc:
        P.validate(long_one)
    assert "72" in str(exc.value)


def test_multibyte_characters_count_as_bytes_not_characters():
    """Emoji are four bytes each, so 20 of them exceed the limit at 20 'characters'."""
    with pytest.raises(P.WeakPassword):
        P.validate("🔐" * 20)


def test_a_null_byte_is_refused():
    """bcrypt treats NUL as a terminator: hashing 'a\\0<anything>' is hashing 'a'."""
    with pytest.raises(P.WeakPassword):
        P.validate("good-passphrase\x00ignored-tail")


def test_hashing_refuses_to_truncate():
    with pytest.raises(P.WeakPassword):
        P.hash_password("x" * 200)


# ── hashing ──────────────────────────────────────────────────────────────────


def test_the_hash_is_bcrypt_and_verifies():
    digest = P.hash_password("correct horse battery staple")
    assert digest.startswith("$2b$")
    assert P.verify_password("correct horse battery staple", digest)
    assert not P.verify_password("correct horse battery stapl", digest)


def test_the_same_password_hashes_differently_every_time():
    """A per-hash salt. Without it, two accounts with the same password have the same
    digest, and one glance at the column says so."""
    a = P.hash_password("correct horse battery staple")
    b = P.hash_password("correct horse battery staple")
    assert a != b and P.verify_password("correct horse battery staple", b)


def test_unicode_is_normalised_so_the_same_phrase_matches_from_any_keyboard():
    """é composed (U+00E9) and é decomposed (e + U+0301) are the same password. Without
    NFKC someone sets it on one machine and cannot sign in from another."""
    digest = P.hash_password("café-passphrase-long")
    assert P.verify_password("café-passphrase-long", digest)


# ── verification never raises ────────────────────────────────────────────────


@pytest.mark.parametrize("stored", [
    None, "", "not-a-hash",
    # The seed's placeholder — a real row in this repo that is not a valid digest.
    "$2b$12$PlenumDemoSeedOnlyNotForLoginUse0000000000000000000",
    "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$hash",
])
def test_an_unreadable_stored_hash_is_a_failed_login_not_a_crash(stored):
    """A malformed digest must mean 'does not match'. Raising here turns a bad row into a
    500 on the login endpoint, which is both an outage and a signal."""
    assert P.verify_password("anything at all", stored) is False


# ── the timing equaliser ─────────────────────────────────────────────────────


def test_burning_time_costs_about_what_a_real_check_costs():
    """Without this, an address with no account answers in ~1ms and a wrong password in
    ~250ms — and the generic 'email and password do not match' is decoration, because the
    response time answers the question the message refuses to.
    """
    digest = P.hash_password("correct horse battery staple")

    t0 = time.perf_counter()
    P.verify_password("wrong wrong wrong wrong", digest)
    real = time.perf_counter() - t0

    t0 = time.perf_counter()
    P.burn_time()
    burned = time.perf_counter() - t0

    # burn_time hashes AND verifies, so it costs at least as much as a verify. The bound
    # is loose on purpose: this pins the order of magnitude, not the hardware.
    assert burned >= real * 0.5, f"burn {burned:.3f}s vs verify {real:.3f}s"
