""""I want to migrate my data" - answered with the three ways in, before any agent runs.

A request to migrate that names no method used to reach the migration agent, whose skill knows two
paths (spreadsheets and live Fiix) and picked one, or the router, which asked a clarifying question
about registers. The user's rule (5 Oct 2026): a bare migration request offers three options -
CSV/Excel data, PDF/documents, or a direct database connection - and each option says the one
thing to do next. Those next steps (attach files, "connect Fiix") already route correctly, so the
reply needs no follow-up state.

Deterministic and free: a regex, no model call. It only fires when the message asks to bring data
in AND names no method; "migrate this CSV", "connect Fiix" or "status of my migration" go on to
the normal routing untouched.
"""
from __future__ import annotations

import re

#: Asking to bring data in.
_WANTS = re.compile(
    r"\b(migrat\w*|import\w*|onboard\w*|move|moving|bring|transfer\w*|load|upload|switch|switching)\b", re.I)
#: ...as a request, not a question about a migration that already ran.
_REQUEST = re.compile(
    r"(^\s*(please\s+)?(migrat|import|onboard|move|bring|transfer|load|upload)\w*\b"
    r"|\bi\s*(want|need|would like|'?d like|wanna|am looking|plan|have)\s+to\b"
    r"|\b(help me|can you|could you|how (do|can|should) (i|we)|let'?s|start|begin|get started|we want|we need)\b)",
    re.I)
#: Data, broadly - or the migrate verb itself, which needs no object.
_DATA = re.compile(
    r"\b(data|records?|cmms|system|everything|assets?|information|history|portfolio|database|files?|"
    r"work orders?|certificates?|documents?)\b|\bmigrat", re.I)
#: A method already named: the normal routing takes it from here.
_METHOD = re.compile(
    r"\b(csv|excel|xlsx?|spreadsheets?|workbooks?|sheets?|pdfs?|docx?|word|scans?|"
    r"sql|postgres\w*|mysql|mssql|sql server|oracle|mongo\w*|odata|api|fiix|maximo|sap|planon|concept|"
    r"connect\w*|sync\w*|attached|uploaded)\b", re.I)
#: About a migration, not a request for one.
_ABOUT = re.compile(
    r"\b(status|progress|history of|how many|cost|failed|stuck|last|previous|earlier|already|"
    r"which migration|what happened|gate|gates|resume|cancel|rollback|roll back)\b", re.I)


def is_bare_migration_request(message: str, extra_context: str | None = None) -> bool:
    """True when the message asks to bring data in and names no way of doing it."""
    msg = " ".join((message or "").split())
    if not msg or len(msg) > 240:
        return False
    if not (_WANTS.search(msg) and _REQUEST.search(msg) and _DATA.search(msg)):
        return False
    if _METHOD.search(msg) or _ABOUT.search(msg):
        return False
    # Files arrived with the turn: the upload decides the path, not a menu.
    ctx = (extra_context or "").lower()
    if any(w in ctx for w in ("uploaded file", "attached file", "file_paths", "migration_id")):
        return False
    return True


CHOOSER_REPLY = """There are three ways to bring your data into Hoistra. Which one fits what you have?

**1. CSV / Excel data migration**
Exports from your current CMMS (Maximo, SAP, Planon, Fiix or any other): assets, locations, work orders, PPM schedules, parts, vendors.
Hoistra reads the columns, maps them to its fields, checks the building and asset hierarchy, and stops at three approval gates before anything is written.
*To start:* attach the CSV or Excel files here, or open **Migration** in the menu.

**2. PDF / document migration**
Certificates, contracts, invoices, inspection and condition reports, O&M manuals.
Each document is classified, its key fields are extracted (expiry dates, certificate numbers, contract terms, amounts), it is linked to its building, and its text is indexed so chat can search it.
*To start:* attach the PDF or Word files here.

**3. Direct database migration**
Connect your live system and pull from it directly, so there are no exports to keep up to date.
Fiix connects from chat: say **connect Fiix** and I will ask for its API keys, fetch the schema, map it and sync.
SQL Server, PostgreSQL, MySQL, MongoDB and OData/REST sources connect through the platform's connectors, which an administrator sets up.

Tell me which one, or just attach the files."""


#: The same three options as cards the chat renders (Chat.jsx / OrchestratorDock.jsx). The text
#: above stays the answer for anything that reads text - the REST API, history, an email - and the
#: chat shows these in its place. `action` is what a click does: `attach` opens the file picker
#: for those types and sends them (a spreadsheet starts a migration, a document is ingested);
#: `ask` sends the text as the next question.
CHOICES: list[dict] = [
    {"id": "csv_excel", "n": 1, "icon": "ph-file-xls", "title": "CSV / Excel data migration",
     "detail": "Exports from your current CMMS (Maximo, SAP, Planon, Fiix or any other): assets, locations, "
               "work orders, PPM schedules, parts and vendors. Fields are mapped, the hierarchy is checked, "
               "and nothing is written until you approve three gates.",
     "cta": "Choose CSV or Excel files",
     "action": {"kind": "attach", "accept": ".csv,.xls,.xlsx"}},
    {"id": "documents", "n": 2, "icon": "ph-file-pdf", "title": "PDF / document migration",
     "detail": "Certificates, contracts, invoices, inspection and condition reports, O&M manuals. Each is "
               "classified, its key fields extracted, linked to its building and made searchable.",
     "cta": "Choose PDF or Word files",
     "action": {"kind": "attach", "accept": ".pdf,.doc,.docx"}},
    {"id": "database", "n": 3, "icon": "ph-database", "title": "Direct database migration",
     "detail": "Connect your live system and pull from it directly. Fiix connects from chat; SQL Server, "
               "PostgreSQL, MySQL, MongoDB and OData/REST sources connect through connectors an "
               "administrator sets up.",
     "cta": "Connect Fiix",
     "action": {"kind": "ask", "text": "connect Fiix"}},
]
