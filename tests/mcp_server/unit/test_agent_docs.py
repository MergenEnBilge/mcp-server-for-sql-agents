"""The documentation for agents must keep telling the truth: every tool and every error message it
mentions has to still exist in the code."""

import re
from pathlib import Path

import pytest

from mcp_sql_server.docs import DOCUMENTS, read_doc
from mcp_sql_server.services.permission_service import TOOL_NAMES

PACKAGE = Path(__file__).resolve().parents[3] / "mcp-server" / "mcp_sql_server"
SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in PACKAGE.rglob("*.py"))


def test_the_guide_mentions_every_tool():
    guide = read_doc("guide.md")
    for tool in TOOL_NAMES | {"get_my_access"}:
        assert f"`{tool}`" in guide, f"guide.md doesn't describe {tool}"


def test_every_document_is_readable_and_not_empty():
    for _uri, (filename, description) in DOCUMENTS.items():
        assert len(read_doc(filename)) > 500 and description


@pytest.mark.parametrize(
    "phrase",
    [
        "has not been approved yet",
        "blocked this agent",
        "approval has expired",
        "too many are already waiting",
        "does not say which application",
        "Too many requests",
        "requests running",
        "You are not permitted to use the",
        "This agent is not permitted to use the",
        "was not found or is not available to you",
        "Only read-only SELECT queries are allowed",
        "Only a single SQL statement is allowed",
        "is not allowed",
        "could not be parsed as a single SELECT query",
        "Backslashes inside quoted text are not supported",
        "exceeded the",
        "row_limit must be between",
        "is currently unavailable",
        "Something went wrong on the server",
    ],
)
def test_each_error_the_guide_explains_still_exists_in_the_code(phrase):
    assert phrase in read_doc("errors.md"), f"errors.md should explain: {phrase}"
    normalised = re.sub(r'"\s*\n\s*f?"', "", SOURCE)  # join strings split across lines
    assert phrase in normalised, f"nothing in the code says: {phrase}"
