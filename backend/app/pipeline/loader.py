import os
from typing import List


def load_document(file_path: str) -> List[dict]:
    """
    加载文档并返回统一格式的页面列表。
    返回: list[{"text": str, "page": int, "filename": str}]
    """
    suffix = os.path.splitext(file_path)[1].lower()

    if suffix == ".pdf":
        return _load_pdf(file_path)
    elif suffix in [".docx", ".doc"]:
        return _load_docx(file_path)
    elif suffix in [".xlsx", ".xls"]:
        return _load_xlsx(file_path)
    elif suffix in [".txt", ".md"]:
        return _load_txt(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}")


def _load_pdf(file_path: str) -> List[dict]:
    import pdfplumber

    pages = []
    filename = os.path.basename(file_path)
    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if text and text.strip():
                pages.append({
                    "text": text.strip(),
                    "page": page_num,
                    "filename": filename,
                })
    return pages


def _load_docx(file_path: str) -> List[dict]:
    from docx import Document

    pages = []
    filename = os.path.basename(file_path)
    doc = Document(file_path)
    full_text = []
    for para in doc.paragraphs:
        if para.text.strip():
            full_text.append(para.text.strip())

    # 简单处理：整个文档作为一个page
    if full_text:
        pages.append({
            "text": "\n".join(full_text),
            "page": 1,
            "filename": filename,
        })
    return pages


def _load_xlsx(file_path: str) -> List[dict]:
    from openpyxl import load_workbook

    pages = []
    filename = os.path.basename(file_path)
    wb = load_workbook(file_path, data_only=True)
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        rows = []
        for row in sheet.iter_rows(values_only=True):
            row_text = " | ".join([str(c) if c is not None else "" for c in row])
            if row_text.strip():
                rows.append(row_text)

        if rows:
            pages.append({
                "text": "\n".join(rows),
                "page": 1,
                "filename": f"{filename} - {sheet_name}",
            })
    return pages


def _load_txt(file_path: str) -> List[dict]:
    filename = os.path.basename(file_path)
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if text:
        return [{"text": text, "page": 1, "filename": filename}]
    return []