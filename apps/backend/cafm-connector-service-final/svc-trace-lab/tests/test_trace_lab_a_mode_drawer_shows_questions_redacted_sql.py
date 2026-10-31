"""A question is redacted wherever a record leaves the server (design spec 3.9). The mode drawer lists a mode's positive
answers, close negatives and close-negative candidates with each answer's question; the question comes from
`labels.turn_rows(..., with_question=True)`, from the chat's row or, once the chat has purged it, from the answer's
evidence snapshot. Both are shown redacted (a Bearer token, an `sk-` key) and as the asker's own words (a wrapped prompt
is cut back to what was typed), redacted before the cut to 200 characters so a secret that straddles the cut cannot
survive in part. The first block is pure; the rest needs the scratch database."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels, normalize, taxonomy

DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "", "level": "technical", "agent": "wo_engine"}
BEARER = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"
SK_KEY = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
QUESTION = f"Why does this fail with {BEARER} and the key {SK_KEY} set?"
SECRETS = ("abcdefghijklmnopqrstuvwxyz0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _clean(shown: str) -> bool:
    return "[redacted" in shown and not any(s in shown for s in SECRETS)


# ── pure: what a listed question looks like ──────────────────────────────────────────────

def test_a_bearer_token_and_a_provider_key_are_redacted():
    shown = labels.shown_question(QUESTION)
    assert _clean(shown) and shown.startswith("Why does this fail with Bearer [redacted]")


def test_a_wrapped_prompt_is_cut_back_to_the_askers_words_before_it_is_shown():
    wrapped = f"Page context with {BEARER}{normalize.QUESTION_MARKER}How many jobs are open?"
    assert labels.shown_question(wrapped) == "How many jobs are open?"


def test_the_secret_is_redacted_before_the_question_is_cut_to_200_characters():
    # The token starts 4 characters before the cut: cutting first would leave "Bearer abcd", too short to be redacted.
    straddling = "a" * 188 + " Bearer " + "abcdefghijklmnop" + " and more text after it"
    shown = labels.shown_question(straddling)
    assert len(shown) == 200 and "abcd" not in shown
    assert shown == ("a" * 188 + " Bearer [redacted] and more text after it")[:200]


@pytest.mark.parametrize("raw", [None, ""])
def test_an_empty_question_stays_empty(raw):
    assert labels.shown_question(raw) == ""


# ── scratch ───────────────────────────────────────────────────────────────────────────────

async def _turn(main, n, question=QUESTION, org=fx.ORG_A):
    t = f"turn-d3{n:014d}"
    async with main.begin() as conn:
        await fx.add_turn(conn, t, org, question=question)
    return t


async def _mode_with_listed_answers(lab, main):
    """A confirmed mode with a positive, a close negative and (not listed) a close-negative candidate, all asking
    the same question with a token and a key in it."""
    pos, neg, cand = await _turn(main, 1), await _turn(main, 2), await _turn(main, 3)
    async with lab.begin() as conn:
        note = await fx.add_note(conn, pos, fx.ORG_A)
    mode = (await taxonomy.create(fx.owner(), DRAFT, created_from=[note]))["mode"]
    await taxonomy.edit(fx.owner(), mode["id"], {"positive_turn_ids": [pos], "close_negative_turn_ids": [neg]},
                        base_version=mode["version"])
    async with lab.begin() as conn:
        await fx.add_label(conn, cand, mode["id"], present=False)
    return mode["id"], pos, neg, cand


@requires_scratch
@pytest.mark.parametrize("who,org", [(fx.owner(), None), (fx.admin(), fx.ORG_A)])
async def test_the_drawer_shows_every_listed_question_redacted(lab_scratch, main_scratch, who, org):
    mid, pos, neg, cand = await _mode_with_listed_answers(lab_scratch, main_scratch)
    drawer = await taxonomy.get_mode(who, org, mid)
    assert [i["turn_id"] for i in drawer["positives"]] == [pos]
    assert [i["turn_id"] for i in drawer["close_negatives"]] == [neg]
    assert [i["turn_id"] for i in drawer["close_negative_candidates"]] == [cand]
    for item in drawer["positives"] + drawer["close_negatives"] + drawer["close_negative_candidates"]:
        assert _clean(item["question"]), item
    assert not any(s in json.dumps(drawer, default=str) for s in SECRETS)


@requires_scratch
async def test_a_wrapped_prompt_in_the_chat_row_is_shown_as_the_askers_words(lab_scratch, main_scratch):
    wrapped = f"Page context with {BEARER}{normalize.QUESTION_MARKER}How many jobs are open?"
    t = await _turn(main_scratch, 4, question=wrapped)
    rows = await labels.turn_rows([t], with_question=True)
    assert rows[t]["question"] == "How many jobs are open?"
    assert rows[t]["organization_id"] == fx.ORG_A


@requires_scratch
async def test_a_question_read_from_the_evidence_snapshot_is_redacted_too(lab_scratch, main_scratch):
    gone = "turn-d3snapshot0000001"                 # the chat no longer has it: only the lab database's copy remains
    turn = {"turn_id": gone, "session_id": "sess-1", "organization_id": fx.ORG_A, "question": QUESTION,
            "answer": "It fails.", "ok": True}
    async with lab_scratch.begin() as conn:
        await conn.execute(text("INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) "
                                "VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'labelled')"),
                           {"t": gone, "o": fx.ORG_A, "j": json.dumps(turn)})
    rows = await labels.turn_rows([gone], with_question=True)
    assert rows[gone]["organization_id"] == fx.ORG_A and _clean(rows[gone]["question"])


@requires_scratch
async def test_turn_rows_without_the_question_carries_none(lab_scratch, main_scratch):
    t = await _turn(main_scratch, 5)
    assert "question" not in (await labels.turn_rows([t]))[t]
