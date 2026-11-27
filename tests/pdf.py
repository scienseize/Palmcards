"""A small text PDF written by hand, for the slide tests: one page per list of
lines (an empty list: a page with no text), Helvetica, 16:9. A line is set at
40 pt if it is the page's first, else 26 pt, or as given: ("text", size)."""


def make_pdf(pages: list[list[str]], size: tuple[int, int] = (960, 540)) -> bytes:
    w, h = size
    n = len(pages)
    # Objects: 1 catalog, 2 page tree, 3 font, then per page its page object and its content.
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    for i, lines in enumerate(pages):
        ops = ["0.95 0.95 0.95 rg", f"0 0 {w} {h} re f", "0 0 0 rg"]
        for k, line in enumerate(lines):
            line, size_pt = line if isinstance(line, tuple) else (line, 40 if k == 0 else 26)
            text = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 {size_pt} Tf 60 {h - 90 - 60 * k} Td ({text}) Tj ET")
        stream = "\n".join(ops).encode("cp1252")  # WinAnsi: the bullet is 0x95
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for k, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % k + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)
