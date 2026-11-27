"""Slides from a PDF (Keynote, PowerPoint and Google Slides all export one),
read with macOS's own Quartz: its CoreGraphics draws the pages, its PDFKit
reads their text. No new dependency: pyobjc-framework-quartz is already
installed for the speech engine.

A PDF is used two ways (CLAUDE.md, "Slides"): as the speech itself, where
its notes are the outline of the slides' titles (palmcards.notes), and as
slides attached to written notes. Either way a full take shows the current
slide in the hand zone and a clicker moves it.

Only a slide's title is read, not its other text: a designed slide's text
is eyebrows set letter by letter, lines wrapped mid-sentence, captions and
footers, none of it what is said. Its title is the largest text on it that
is words, not figures (a slide of statistics sets its numbers largest)."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import numpy as np


@contextlib.contextmanager
def _quiet_stderr():
    """PDFKit logs two lines per page read ("attributedStringScaled") straight to file 2."""
    saved = os.dup(2)
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def _words(text: str) -> bool:
    """Words rather than figures: more letters than digits."""
    return sum(c.isalpha() for c in text) > sum(c.isdigit() for c in text)


def _nsdata(data: bytes):
    from Foundation import NSData
    return NSData.dataWithBytes_length_(data, len(data))


def page_titles(data: bytes) -> list[str | None]:
    """Each page's title: its text in the largest font size that reads as words (PDFKit), on one
    line; None for a page with no words (an image, or figures only)."""
    from Quartz import PDFDocument
    doc = PDFDocument.alloc().initWithData_(_nsdata(data))
    if doc is None:
        raise ValueError("Not a PDF that macOS can read.")
    with _quiet_stderr():
        texts = [doc.pageAtIndex_(i).attributedString() for i in range(doc.pageCount())]
    titles = []
    for text in texts:
        runs, at = [], 0
        while text is not None and at < text.length():
            attrs, span = text.attributesAtIndex_effectiveRange_(at, None)
            font = attrs.get("NSFont")
            runs.append((font.pointSize() if font is not None else 0.0,
                         text.string()[span.location:span.location + span.length]))
            at = span.location + span.length
        by_size: dict[float, list[str]] = {}
        for size, part in runs:
            if part.strip():
                by_size.setdefault(size, []).append(" ".join(part.split()))
        title = next((t for _, parts in sorted(by_size.items(), reverse=True)
                      if _words(t := " ".join(parts))), None)
        titles.append(title)
    return titles


class Deck:
    """A PDF's pages, each drawn when first shown at `width` px (white behind it) as a BGR
    array, the last few kept: ~2 ms at the hand zone's width, ~5 at 1280 px (a 39-page deck)."""

    KEEP = 6  # pages kept drawn

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
        self._drawn: dict[int, np.ndarray] = {}

    @classmethod
    def open(cls, path: Path, width: int) -> "Deck":
        return cls(Path(path).read_bytes(), width, Path(path).name)

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

    def page(self, i: int) -> np.ndarray:
        """Page i (clamped to the deck), drawn now if it isn't kept."""
        i = min(max(i, 0), self.pages - 1)
        if i not in self._drawn:
            if len(self._drawn) >= self.KEEP:
                self._drawn.pop(next(iter(self._drawn)))  # the oldest drawn
            self._drawn[i] = self.render(i)
        return self._drawn[i]
