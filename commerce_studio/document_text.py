"""Extract readable merchant text while retaining the original document."""

from io import BytesIO


LIMIT = 12000


def extract(raw: bytes, mime: str) -> str:
    if mime == "text/plain":
        try:
            return raw.decode("utf-8-sig")[:LIMIT]
        except UnicodeDecodeError:
            return ""
    if mime == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(raw))
        if reader.is_encrypted:
            return ""
        parts = []
        length = 0
        for page in reader.pages:
            value = page.extract_text() or ""
            parts.append(value)
            length += len(value)
            if length >= LIMIT:
                break
        return "\n".join(parts)[:LIMIT]
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        from docx import Document

        document = Document(BytesIO(raw))
        parts = []
        length = 0
        for item in document.iter_inner_content():
            if hasattr(item, "rows"):
                value = "\n".join(" | ".join(cell.text for cell in row.cells) for row in item.rows)
            else:
                value = item.text
            parts.append(value)
            length += len(value)
            if length >= LIMIT:
                break
        return "\n".join(parts)[:LIMIT]
    return ""
