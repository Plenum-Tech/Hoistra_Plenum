"""The request a subject is about, whatever number of "Reminder: " is in front of it."""
from src.shared.approvals import base_subject


def test_reminders_fold_back_to_the_request():
    s = "Work order request — Lighting control panel — Basement · Bishopsgate Tower"
    assert base_subject(s) == s
    assert base_subject("Reminder: " + s) == s
    assert base_subject("reminder: Reminder:  " + s) == s
    assert base_subject("") == ""
