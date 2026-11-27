"""Slides from a PDF (Keynote, PowerPoint and Google Slides all export one),
read with macOS's own Quartz: its CoreGraphics draws the pages, its PDFKit
reads their text. No new dependency: pyobjc-framework-quartz is already
installed for the speech engine.

A PDF is used two ways (CLAUDE.md, "Slides"): as the speech itself, where
each page with text is a section titled "Slide N" (palmcards.notes), and as
slides attached to written notes. Either way a full take shows the current
slide in the hand zone and a clicker moves it."""

from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np

BULLETS = "•◦▪▫‣⁃–—-*·●○■□►▸"
SLIDE_TITLE = re.compile(r"^Slide (\d+)$")


def _nsdata(data: bytes):
    from Foundation import NSData
    return NSData.dataWithBytes_length_(data, len(data))


def page_texts(data: bytes) -> list[list[str]]:
    """Each page's text lines (PDFKit), bullet marks and lines of only digits
    (page numbers) left out. A page with no text: an empty list."""
    from Quartz import PDFDocument
    doc = PDFDocument.alloc().initWithData_(_nsdata(data))
    if doc is None:
        raise ValueError("Not a PDF that macOS can read.")
    pages = []
    for i in range(doc.pageCount()):
        text = doc.pageAtIndex_(i).string() or ""
        lines = []
        for raw in text.splitlines():
            line = " ".join(raw.split()).lstrip(BULLETS).strip()
            if line and not line.isdigit():
                lines.append(line)
        pages.append(lines)
    return pages


def slide_of(title: str) -> int | None:
    """The 0-based slide of a section titled "Slide N" (a PDF read as notes), else None.
    Read back from the title, so it survives notes snapshots and edits (edits never touch titles)."""
    m = SLIDE_TITLE.match(title or "")
    return int(m.group(1)) - 1 if m else None


class Deck:
    """A PDF's pages, each drawn once at `width` px (white behind it) as a BGR
    array, in a thread of its own so opening a deck never stalls a frame."""

    def __init__(self, data: bytes, width: int, name: str = ""):
        import Quartz
        self.name = name
        provider = Quartz.CGDataProviderCreateWithCFData(_nsdata(data))
        self._doc = Quartz.CGPDFDocumentCreateWithProvider(provider)
        if self._doc is None:
            raise ValueError(f"{name or 'The file'} is not a PDF that macOS can read.")
        self.pages = Quartz.CGPDFDocumentGetNumberOfPages(self._doc)
        if self.pages == 0:
            raise ValueError(f"{name or 'The PDF'} has no pages.")
        self.width = width
        self._images: list[np.ndarray | None] = [None] * self.pages
        self._thread = threading.Thread(target=self._render_all, name="slides", daemon=True)
        self._thread.start()

    @classmethod
    def open(cls, path: Path, width: int) -> "Deck":
        return cls(Path(path).read_bytes(), width, Path(path).name)

    def _render_all(self) -> None:
        for i in range(self.pages):
            self._images[i] = self.render(i)

    def render(self, i: int) -> np.ndarray:
        """Page i (0-based) as a BGR array `width` px wide, at the page's aspect."""
        import Quartz
        page = Quartz.CGPDFDocumentGetPage(self._doc, i + 1)
        box = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFCropBox)
        scale = self.width / max(1.0, box.size.width)
        w, h = self.width, max(1, round(box.size.height * scale))
        ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(),
                                           Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))
        Quartz.CGContextScaleCTM(ctx, scale, scale)
        Quartz.CGContextTranslateCTM(ctx, -box.origin.x, -box.origin.y)
        Quartz.CGContextDrawPDFPage(ctx, page)
        image = Quartz.CGBitmapContextCreateImage(ctx)
        row = Quartz.CGImageGetBytesPerRow(image)
        raw = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image)))
        rgba = np.frombuffer(raw, np.uint8).reshape(h, row // 4, 4)[:, :w]
        return np.ascontiguousarray(rgba[:, :, 2::-1])  # RGBA -> BGR

    def page(self, i: int) -> np.ndarray | None:
        """Page i once drawn, else None (still drawing)."""
        return self._images[i] if 0 <= i < self.pages else None

    @property
    def ready(self) -> bool:
        return all(im is not None for im in self._images)
