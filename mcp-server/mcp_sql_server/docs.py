"""The documentation written for AI agents, shipped inside the package.

The same text is offered three ways, so an agent finds it whichever way its client works: as MCP
resources (for clients that read them), as prompts (for clients that list them as shortcuts), and
over plain HTTP at /agent-guide (for anyone, before connecting). The files are ordinary Markdown
in the `docs` folder next to this module.
"""

from functools import cache
from importlib import resources

GUIDE_URI = "sql-data-layer://guide"
DIALECTS_URI = "sql-data-layer://dialects"
ERRORS_URI = "sql-data-layer://errors"

# uri -> (file, one-line description shown next to the resource in a client)
DOCUMENTS = {
    GUIDE_URI: (
        "guide.md",
        "How to use this server: the tools, a good order of work, and the limits.",
    ),
    DIALECTS_URI: (
        "dialects.md",
        "SQL differences between PostgreSQL, MySQL, SQL Server and SQLite.",
    ),
    ERRORS_URI: ("errors.md", "What each error message means and what to do about it."),
}


@cache
def read_doc(filename: str) -> str:
    return resources.files("mcp_sql_server").joinpath("docs", filename).read_text(encoding="utf-8")
