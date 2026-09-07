"""Vision-based image understanding service.

Uses OpenAI's vision-capable chat model (gpt-4o-mini by default) to
extract structured content from images found in documents. Unlike raw
OCR this preserves table structure, handles merged cells, and returns
JSON that we can feed directly into the existing table chunk pipeline.

Behavior:
  - If OPENAI_API_KEY is set → real vision calls (primary path).
  - If not set → service reports unavailable; extraction_service falls
    back to tesseract OCR (or plain image-only indexing).

Cost: one request per image; gpt-4o-mini vision is ~$0.0002 / image.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logger import logger


@dataclass
class VisionResult:
    image_type: str = ""           # table | diagram | photo | text_block | ""
    description: str = ""          # human-readable summary of the image
    tables: list[list[list[str]]] = field(default_factory=list)  # [table][row][cell]
    extra_text: str = ""           # any labels/captions/annotations
    entities: dict[str, list[str]] = field(default_factory=dict)
    raw_response: str = ""         # always stored, for debugging

    @property
    def has_tables(self) -> bool:
        return any(len(t) >= 2 for t in self.tables)

    @property
    def combined_text(self) -> str:
        parts = []
        if self.description:
            parts.append(self.description)
        if self.extra_text:
            parts.append(self.extra_text)
        return "\n".join(parts).strip()


_SYSTEM_PROMPT = """You extract structured data from images embedded in
enterprise documents. Your output is JSON only — never prose, never
markdown fences, never commentary.

# STEP 1 — Classify the image
Decide which of these the image is:
  (a) TABLE — any grid, schedule, matrix, exhibit, or list with rows
      and columns. INCLUDES tables with merged cells, multi-level
      headers, sub-rows, totals rows, and tables drawn with visible
      borders OR aligned whitespace.
  (b) DIAGRAM — flowchart, architecture diagram, floor plan, schematic.
  (c) PHOTO — real-world photograph, equipment shot, signature.
  (d) TEXT_BLOCK — a paragraph, heading, or callout with no structure.

# STEP 2 — If TABLE, you MUST return it under `tables`
This is the most important rule. If you classified the image as a
table, `tables` MUST be a non-empty array. Returning a prose
description of a table instead of structured rows is a FAILURE.

For every table:
  - Extract `headers` as a list of column names, left to right.
  - Extract every data row as a list of cells, in the same order as headers.
  - If a cell spans multiple rows (merged vertically), REPEAT the value
    on each row it covers — never leave a cell blank because of merging.
  - If a cell contains multiple sub-values stacked vertically (e.g. an
    elevator row showing three "Stops" values: 2, 2, 2), emit one row
    per sub-value and repeat the other cells.
  - Include the "Total" row if present.
  - Preserve numbers and units EXACTLY as shown. Do not round or convert.
  - Do not invent columns or rows that aren't visible.

If you classified the image as DIAGRAM/PHOTO/TEXT_BLOCK, return
`tables: []` and put what you read into `description` / `extra_text`.

# STEP 3 — Return this JSON object
{
  "image_type": "table" | "diagram" | "photo" | "text_block",
  "description": "1-3 sentence summary of what the image shows",
  "tables": [
    {
      "title": "the table title if one is visible, else empty string",
      "headers": ["col1", "col2", ...],
      "rows": [
        ["cell", "cell", ...],
        ["cell", "cell", ...]
      ]
    }
  ],
  "extra_text": "any readable text that is NOT inside a table (captions, labels, notes, page numbers)",
  "entities": {
    "asset_codes": [],
    "contract_numbers": [],
    "invoice_numbers": [],
    "dates": [],
    "money": [],
    "other_ids": []
  }
}

# EXAMPLE — this is what a correct response looks like for an elevator schedule image

Input image shows a table titled "BRT - Green Line Elevator Details"
with columns S.No, Station Type, Station Level, Total Stations,
Quantity, Total Quantity, Stops, Single Unit Load (kW), Total Load (kW).
Row 1 has station type A, 8 stations, and the Stops cell shows three
stacked values "2, 2, 2".

Correct output:
{
  "image_type": "table",
  "description": "Elevator details schedule for BRT Green Line showing five station types with lift quantities, stops, and total load.",
  "tables": [
    {
      "title": "BRT - Green Line Elevator Details",
      "headers": ["S.No", "Station Type", "Station Level", "Total Stations", "Quantity", "Total Quantity", "Stops", "Single Unit Load (kW)", "Total Load (kW)"],
      "rows": [
        ["1", "A", "Split Type at Elevated", "8", "3", "24", "2", "10", "240"],
        ["1", "A", "Split Type at Elevated", "8", "3", "24", "2", "10", "240"],
        ["1", "A", "Split Type at Elevated", "8", "3", "24", "2", "10", "240"]
      ]
    }
  ],
  "extra_text": "EXHIBIT 10 - ELEVATOR DETAILS",
  "entities": {"other_ids": ["BRT Green Line"]}
}

Notice: the "Stops 2, 2, 2" sub-rows became three separate rows, each
repeating the station-type-A data. This is what STEP 2's "one row per
sub-value" rule requires.

Return ONLY the JSON object. No code fences. No explanation.
"""


class VisionService:
    def __init__(self) -> None:
        self.model = settings.openai_llm_model
        self.mock = settings.is_mock_mode
        self._client = None
        self._min_dim = 60   # skip tiny decorative images (icons, bullets)

        if not self.mock:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=settings.openai_api_key)
                logger.info("VisionService ready | model={}", self.model)
            except Exception as e:
                logger.error("OpenAI vision client init failed: {}", e)
                self.mock = True

        if self.mock:
            logger.warning(
                "VisionService unavailable (no OPENAI_API_KEY). "
                "Images will fall back to tesseract OCR if available."
            )

    @property
    def available(self) -> bool:
        return not self.mock and self._client is not None

    def should_skip(self, width: int, height: int) -> bool:
        """Return True for images too small to contain useful content."""
        return width < self._min_dim or height < self._min_dim

    def extract_from_image(
        self,
        image_bytes: bytes,
        image_format: str = "png",
    ) -> VisionResult:
        """Return a VisionResult for an image. Empty result on error/mock.

        If the first call classifies the image as a table but returns no
        structured rows, we retry ONCE with a stricter prompt. This is
        the exact failure mode where gpt-4o-mini describes the table in
        prose instead of extracting it.
        """
        if not self.available or not image_bytes:
            return VisionResult()
        try:
            result = self._call_openai(image_bytes, image_format)
        except Exception as e:
            logger.exception("Vision extraction failed: {}", e)
            return VisionResult()

        # Retry if the model said "this is a table" but didn't give us rows.
        needs_retry = (
            result.image_type == "table" and not result.tables
        ) or (
            not result.tables
            and any(
                kw in (result.description or "").lower()
                for kw in ("table", "schedule", "matrix", "exhibit", "rows and columns")
            )
        )
        if needs_retry:
            logger.warning(
                "Vision returned no tables for a table-classified image; "
                "retrying with stricter prompt..."
            )
            try:
                retry_result = self._call_openai_strict(image_bytes, image_format)
                if retry_result.tables:
                    logger.info(
                        "Retry succeeded | tables={}", len(retry_result.tables)
                    )
                    return retry_result
                logger.error(
                    "Retry also failed to produce tables. Image is either "
                    "truly not a table, or the model cannot read it. "
                    "raw={}",
                    retry_result.raw_response[:800],
                )
            except Exception as e:
                logger.exception("Vision retry failed: {}", e)

        return result

    def _call_openai_strict(self, image_bytes: bytes, image_format: str) -> VisionResult:
        """Re-prompt the model with no-prose-allowed instructions.
        Used as a second-chance when the first call describes a table
        instead of extracting it.
        """
        mime = {
            "jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif",
            "webp": "webp", "bmp": "bmp", "tiff": "tiff",
        }.get(image_format.lower(), "png")
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:image/{mime};base64,{b64}"

        strict_user_msg = (
            "Your previous response was wrong. This image IS a table. "
            "You must return structured rows, not a description.\n\n"
            "Output this exact JSON — no prose, no fences:\n"
            '{"image_type": "table", "description": "...", '
            '"tables": [{"title": "...", "headers": [...], "rows": [[...]]}], '
            '"extra_text": "...", "entities": {}}\n\n'
            "The `tables[0].headers` array must contain every visible column "
            "name. The `tables[0].rows` array must contain one entry per "
            "data row, with cells in the same order as headers. Repeat "
            "merged-cell values. Preserve numbers exactly. Do not skip rows."
        )

        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": strict_user_msg},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=4096,
        )
        raw = resp.choices[0].message.content or "{}"
        return self._parse_response(raw)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    def _call_openai(self, image_bytes: bytes, image_format: str) -> VisionResult:
        # Data URL
        mime = {
            "jpg": "jpeg",
            "jpeg": "jpeg",
            "png": "png",
            "gif": "gif",
            "webp": "webp",
            "bmp": "bmp",
            "tiff": "tiff",
        }.get(image_format.lower(), "png")
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:image/{mime};base64,{b64}"

        logger.debug("Vision call | model={} | bytes={}", self.model, len(image_bytes))
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze this image and return the JSON object. "
                                "If it contains any kind of table, grid, or "
                                "structured schedule, the `tables` array MUST be "
                                "non-empty. Do not describe a table in prose — "
                                "extract it."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
            # Bumped from 2000 — large exhibit tables can blow past that,
            # and a truncated JSON response fails to parse entirely.
            max_tokens=4096,
        )
        raw = resp.choices[0].message.content or "{}"
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> VisionResult:
        """Lenient parser that tolerates several common gpt-4o-mini
        output shapes:

          - {"tables": [{"headers": [...], "rows": [[...]]}]}   <- canonical
          - {"tables": [{"columns": [...], "data": [[...]]}]}   <- sloppy key names
          - {"tables": [[["h1","h2"], ["v1","v2"]]]}            <- already flat
          - {"tables": {"headers": [...], "rows": [...]}}       <- single-table as dict

        Always logs the raw response on any anomaly so you can debug.
        """
        try:
            obj = json.loads(raw)
        except Exception as e:
            logger.warning("Vision response was not valid JSON: {} | raw={}",
                           e, raw[:500])
            return VisionResult(raw_response=raw)

        if not isinstance(obj, dict):
            logger.warning("Vision response was not a JSON object | raw={}", raw[:500])
            return VisionResult(raw_response=raw)

        image_type = str(obj.get("image_type") or "").strip().lower()
        raw_tables = obj.get("tables")

        # Coerce a single-dict "tables" into a one-item list
        if isinstance(raw_tables, dict):
            raw_tables = [raw_tables]
        if raw_tables is None:
            raw_tables = []
        if not isinstance(raw_tables, list):
            logger.warning("Vision 'tables' field had unexpected type {} | raw={}",
                           type(raw_tables).__name__, raw[:500])
            raw_tables = []

        tables: list[list[list[str]]] = []
        for t in raw_tables:
            tbl = self._normalize_table(t)
            if tbl:
                tables.append(tbl)

        entities_raw = obj.get("entities") or {}
        entities: dict[str, list[str]] = {}
        if isinstance(entities_raw, dict):
            for k, v in entities_raw.items():
                if isinstance(v, list) and v:
                    entities[k] = [str(x) for x in v if x]

        description = str(obj.get("description") or "").strip()
        extra_text = str(obj.get("extra_text") or "").strip()

        # Warn loudly when the model says "this is a table" but didn't give us one.
        # This is the exact failure mode you hit — now it's visible in the logs.
        if image_type == "table" and not tables:
            logger.error(
                "Vision classified image as TABLE but returned no structured "
                "tables! This is a prompt failure. raw={}",
                raw[:1500],
            )
        elif not tables and ("table" in description.lower() or "column" in description.lower()
                             or "row" in description.lower()):
            logger.warning(
                "Vision description mentions tables/rows/columns but returned "
                "no structured tables. raw={}",
                raw[:800],
            )
        else:
            logger.debug(
                "Vision parsed | type={} | tables={} | desc_len={} | extra_len={}",
                image_type, len(tables), len(description), len(extra_text),
            )

        return VisionResult(
            image_type=image_type,
            description=description,
            tables=tables,
            extra_text=extra_text,
            entities=entities,
            raw_response=raw,
        )

    @staticmethod
    def _normalize_table(t) -> list[list[str]] | None:  # type: ignore[no-untyped-def]
        """Accept several table shapes and return a unified [[headers], [row], ...].
        Returns None if the table has neither headers nor rows we can recover."""
        # Shape A: already a list-of-lists (flat format)
        if isinstance(t, list):
            rows = [
                [str(c).strip() for c in row]
                for row in t
                if isinstance(row, list) and any(row)
            ]
            return rows if len(rows) >= 2 else None

        if not isinstance(t, dict):
            return None

        # Shape B/C: dict with keys. Be lenient on key names.
        headers = (
            t.get("headers")
            or t.get("columns")
            or t.get("header")
            or t.get("column_names")
            or []
        )
        rows = (
            t.get("rows")
            or t.get("data")
            or t.get("values")
            or []
        )

        headers = [str(h).strip() for h in headers if h is not None]
        norm_rows: list[list[str]] = []
        for row in rows:
            if isinstance(row, dict):
                # Row-as-dict {col: val}. Re-key by headers if possible.
                if headers:
                    norm_rows.append([str(row.get(h, "")).strip() for h in headers])
                else:
                    norm_rows.append([str(v).strip() for v in row.values()])
            elif isinstance(row, list):
                cells = [str(c).strip() for c in row]
                if any(cells):
                    norm_rows.append(cells)

        if headers and norm_rows:
            return [headers] + norm_rows
        if norm_rows and len(norm_rows) >= 2:
            # No headers but enough rows — treat first row as header
            return norm_rows
        return None


vision_service = VisionService()
