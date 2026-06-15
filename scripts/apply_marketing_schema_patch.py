from __future__ import annotations

import re
from pathlib import Path

path = Path("fridge_assistant.py")
source = path.read_text(encoding="utf-8")

schema_block = '''def _is_marketing_schema_question(question: str) -> bool:
    question_text = str(question or "").lower()
    schema_phrases = [
        "marketing schema",
        "marketing field",
        "field schema",
        "usp135",
        "tier",
        "\u8425\u9500\u5b57\u6bb5",
        "\u5b57\u6bb5\u89c4\u8303",
        "\u5b57\u6bb5\u5b9a\u4e49",
        "\u6570\u636e\u5e93\u7ec4\u7ec7",
        "\u7ec4\u7ec7\u903b\u8f91",
        "\u5173\u8054\u903b\u8f91",
        "\u4e0a\u6e38\u4f9d\u8d56",
        "\u4e0b\u6e38\u5173\u8054",
        "\u884c\u7ea7\u6570\u636e",
        "\u7236\u8bb0\u5f55",
        "\u591a\u884c\u6570\u636e",
        "\u91d1\u5b57\u5854",
    ]
    return any(phrase in question_text for phrase in schema_phrases)


def _is_marketing_content_question(question: str) -> bool:
    question_text = str(question or "").lower()
    content_phrases = [
        "usp135",
        "usp",
        "tier",
        "\u8425\u9500",
        "\u5356\u70b9",
        "\u573a\u666f",
        "\u4eba\u7fa4",
        "\u7528\u6237",
        "\u52a8\u673a",
        "\u75db\u70b9",
        "\u5b9a\u4f4d",
        "\u8bdd\u672f",
        "\u91d1\u5b57\u5854",
    ]
    return any(phrase in question_text for phrase in content_phrases)
'''

source, schema_count = re.subn(
    r"def _is_marketing_schema_question\(question: str\) -> bool:\n.*?\n\ndef _question_intent",
    schema_block + "\n\ndef _question_intent",
    source,
    flags=re.S,
)
if schema_count != 1:
    raise SystemExit(f"Expected to patch one schema intent block, patched {schema_count}")

source, fallback_count = re.subn(
    r"if marketing_answer and \(not specs or any\(token in question_text for token in \[[^\]]+\]\)\):",
    "if marketing_answer and (not specs or _is_marketing_content_question(question)):",
    source,
    flags=re.S,
)
if fallback_count not in {0, 1}:
    raise SystemExit(f"Unexpected fallback patch count {fallback_count}")

schema_doc_block = '''            if intent == "marketing_schema" and "data_source" in documents:
                document_hits = documents[
                    documents["data_source"].astype(str).str.contains("marketing_schema", case=False, na=False)
                ].head(6)
'''
needle = "        document_hits = pd.DataFrame()\n        if not documents.empty:\n"
if schema_doc_block.strip() not in source:
    source = source.replace(needle, needle + schema_doc_block, 1)

path.write_text(source, encoding="utf-8")
