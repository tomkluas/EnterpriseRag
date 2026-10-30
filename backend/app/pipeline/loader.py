from pathlib import Path


def load_document(file_path: str) -> list[dict]:
    """
    返回 list[{"text": str, "page": int, "filename": str}]
    page 从 1 开始；不支持分页的格式设 page=1
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    filename = path.name

    if suffix == ".pdf":
        return _load_pdf(file_path, filename)
    elif suffix == ".docx":
        return _load_docx(file_path, filename)
    elif suffix in (".xlsx", ".xls"):
        return _load_excel(file_path, filename)
    elif suffix in (".txt", ".md"):
        return _load_text(file_path, filename)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}")


def _load_pdf(file_path: str, filename: str) -> list[dict]:
    import pdfplumber
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append({"text": text, "page": i, "filename": filename})
    return pages


def _load_docx(file_path: str, filename: str) -> list[dict]:
    from docx import Document
    doc = Document(file_path)
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return [{"text": text, "page": 1, "filename": filename}]


def _load_excel(file_path: str, filename: str) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    pages = []
    for i, sheet in enumerate(wb.worksheets, start=1):
        rows = []
        for row in sheet.iter_rows(values_only=True):
            row_text = "\t".join(str(c) if c is not None else "" for c in row)
            if row_text.strip():
                rows.append(row_text)
        if rows:
            pages.append({"text": "\n".join(rows), "page": i, "filename": filename})
    wb.close()
    return pages


def _load_text(file_path: str, filename: str) -> list[dict]:
    with open(file_path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    return [{"text": text, "page": 1, "filename": filename}]
