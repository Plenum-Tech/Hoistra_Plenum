"""OCR service.

Wraps pytesseract so the rest of the pipeline can call OCR without
caring whether tesseract is installed. If the tesseract binary is not
available, every OCR call returns an empty string and logs a warning —
image extraction still happens, only the text layer is missing.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from app.core.logger import logger


class OCRService:
    def __init__(self) -> None:
        self.available = False
        self._pytesseract = None
        self._Image = None
        self._version: str | None = None
        try:
            import pytesseract
            from PIL import Image
            # Probe the binary — will raise if tesseract isn't installed
            version = pytesseract.get_tesseract_version()
            self._pytesseract = pytesseract
            self._Image = Image
            self._version = str(version)
            self.available = True
            logger.info("OCR service ready | tesseract={}", self._version)
        except Exception as e:
            logger.warning(
                "OCR service unavailable ({}). Image text will NOT be extracted. "
                "Install tesseract-ocr system package + `pip install pytesseract Pillow` "
                "to enable.", e,
            )

    # ---------- public API ----------
    def ocr_bytes(self, data: bytes, lang: str = "eng") -> str:
        """OCR a raw image (PNG/JPEG bytes). Returns extracted text or ''."""
        if not self.available or not data:
            return ""
        try:
            img = self._Image.open(BytesIO(data))
            return self._ocr_pil(img, lang)
        except Exception as e:
            logger.warning("OCR on bytes failed: {}", e)
            return ""

    def ocr_file(self, path: str | Path, lang: str = "eng") -> str:
        """OCR an image file on disk."""
        if not self.available:
            return ""
        try:
            img = self._Image.open(Path(path))
            return self._ocr_pil(img, lang)
        except Exception as e:
            logger.warning("OCR on file {} failed: {}", path, e)
            return ""

    def ocr_pil(self, img, lang: str = "eng") -> str:  # type: ignore[no-untyped-def]
        """OCR a PIL Image instance directly (used when rendering PDF pages)."""
        if not self.available:
            return ""
        try:
            return self._ocr_pil(img, lang)
        except Exception as e:
            logger.warning("OCR on PIL image failed: {}", e)
            return ""

    def _ocr_pil(self, img, lang: str) -> str:  # type: ignore[no-untyped-def]
        # Convert palette / CMYK images to RGB so tesseract is happy.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        text = self._pytesseract.image_to_string(img, lang=lang)
        return (text or "").strip()


ocr_service = OCRService()
