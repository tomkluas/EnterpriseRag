import pytest
import os
import tempfile
from app.pipeline.loader import load_document
from app.pipeline.splitter import split_pages


def test_load_txt():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write("这是测试文本内容。\n第二行内容。")
        temp_path = f.name

    try:
        pages = load_document(temp_path)
        assert len(pages) == 1
        assert "这是测试文本内容" in pages[0]["text"]
        assert pages[0]["page"] == 1
    finally:
        os.unlink(temp_path)


def test_split_pages():
    pages = [
        {"text": "这是第一段文本。", "page": 1, "filename": "test.txt", "doc_id": "doc1", "kb_id": "kb1"},
        {"text": "这是第二段文本。", "page": 2, "filename": "test.txt", "doc_id": "doc1", "kb_id": "kb1"},
    ]

    chunks = split_pages(pages)
    assert len(chunks) > 0
    for chunk in chunks:
        assert "text" in chunk
        assert "metadata" in chunk
        assert chunk["metadata"]["doc_id"] == "doc1"


def test_unsupported_format():
    with tempfile.NamedTemporaryFile(suffix='.unknown', delete=False) as f:
        temp_path = f.name

    try:
        with pytest.raises(ValueError, match="不支持的文件格式"):
            load_document(temp_path)
    finally:
        os.unlink(temp_path)