"""Hoist Trace Lab: an evaluation module for the Hoist AI chat.

Trace Lab measures how well the Hoist chat answers real questions. It reads golden question
sets (Excel or CSV), grades recorded chat turns with deterministic checks, and reports the
results with honest statistics. It is built as a separate module that sits beside the
application, in its own folder (svc-trace-lab), not inside it.

The Golden Rule (G0)
--------------------
Existing application logic is never edited for Trace Lab, and the existing application must
not be affected by it. Existing code is reused only by importing it and calling it from here.
Nothing flows the other way:

* No existing module imports this package. tests/test_trace_lab_golden_rule.py scans every
  Python file in every other service folder for any import of it and fails unless the
  attachment has been approved explicitly in that test's ALLOWED_ATTACHMENTS list.
* svc-deepagents holds nothing of Trace Lab: no file or folder in its src/ or tests/ is named
  after it, so that service's own test baseline never changes.
* Trace Lab may read the application's tables in the plenum_cafm schema, but it never writes
  to or changes an object there; the same test scans this folder (outside tests/) for SQL
  that would. Its own data lives in its own schema.
* Importing this package loads no application module, so the application behaves identically
  with or without Trace Lab. Modules inside the package that reuse application code (for
  example the write-intent check that svc-deepagents already owns) import it explicitly from
  svc-deepagents' own src package, and call it without editing it.

Trace Lab runs as its own process, started on its own. The application never starts it, and
it is never part of the chat service's request path. It reuses svc-deepagents code by import
(that folder is put on the path by this folder's pyproject.toml for tests), and uses nothing
beyond the standard library.
"""
