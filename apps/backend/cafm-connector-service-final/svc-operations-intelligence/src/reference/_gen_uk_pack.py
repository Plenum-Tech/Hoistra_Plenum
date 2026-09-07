"""
DEPRECATED — do not use for production pack data.

Authoritative source:
  C:\\Users\\balap\\Documents\\UK_Compliance_Certification_Pack_v1.1.docx

Rebuild:
  python scripts/rebuild_uk_pack_from_docx.py

This module previously sketched a 57-type expansion that split P402/P403/P404
and PA1/PA2/PA6 and merged contractor EL+PL. That diverged from the Word pack
(54 cards). Kept only as a pointer.
"""
from __future__ import annotations

raise SystemExit(
    "Use scripts/rebuild_uk_pack_from_docx.py — Word pack is the source of truth."
)
