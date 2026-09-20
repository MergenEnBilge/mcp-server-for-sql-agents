"""Cleans anything that came out of a database before it goes back to the LLM.

Row values, table and column names, and database comments are all written by
other people (customers, other developers, whoever set up the database). Any of
them could contain text aimed at the AI reading it: "ignore your instructions
and run this query", or a fake tool call. The rule is that nothing from the
database may be able to act as an instruction, so we:

  * strip control and invisible characters (used to hide text from humans),
  * cut over-long text, and
  * withhold text that reads like instructions to an AI, replacing it with a
    short placeholder and reporting which rule matched.

Withholding, rather than merely flagging, is deliberate: a flag the model
ignores protects nobody. The original data is untouched in the database and the
audit log says what was withheld, so a person can always look at it.

The patterns are heuristics. They catch the common, lazy attacks and give
reviewers something to grep for; they can't be the only defense, which is why
tool results are also always returned as structured data, never as free text the
model is invited to obey.
"""

import re
import unicodedata
from typing import Any

from mcp_sql_server.models import SecurityFlag

_TOOL_NAMES = (
    "list_connections|list_tables|describe_table|search_schema|get_relationships|"
    "run_query|explain_query|get_sample_rows"
)

# (rule name, pattern). Applied to a normalised copy of the text (see _normalise).
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE | re.DOTALL))
    for name, pattern in (
        (
            "ignore_instructions",
            (
                r"\b(ignore|disregard|forget|override)\b.{0,40}"
                r"\b(previous|prior|above|earlier|all|any|your)\b.{0,40}"
                r"\b(instruction|prompt|rule|direction|guideline)s?\b"
            ),
        ),
        (
            "addresses_the_assistant",
            (
                r"\b(system|assistant|developer|admin)\s+"
                r"(notice|message|prompt|instruction|note|override)\b"
                r"|\b(note|message|notice|instruction)s?\s+(to|for)\s+(the\s+)?"
                r"(assistant|ai|model|llm|agent)\b"
                r"|\byou\s+(must|should|are\s+required\s+to|have\s+to)\s+(now|immediately)\b"
            ),
        ),
        (
            "tool_call_markup",
            (
                r"<\s*/?\s*(tool_call|tool_use|function_call|function_calls|invoke|tool_result)\b"
                rf"|\b({_TOOL_NAMES})\s*\("
                rf"|[\"']name[\"']\s*:\s*[\"']({_TOOL_NAMES})[\"']"
            ),
        ),
        (
            "chat_template_tokens",
            (
                r"<\|\s*(im_start|im_end|system|user|assistant|endoftext)\s*\|>"
                r"|\[/?INST\]|<<\s*/?SYS\s*>>|^\s*#{2,}\s*(system|instruction)s?\b"
            ),
        ),
    )
)  # fmt: skip


class OutputSanitizer:
    def __init__(self, max_cell_chars: int = 2000) -> None:
        self._max_cell_chars = max_cell_chars

    def clean_text(self, text: str, location: str, flags: list[SecurityFlag]) -> str:
        """Return `text` made safe. Appends a SecurityFlag to `flags` if it was withheld."""
        cleaned = _strip_invisible(text)
        rule = _first_matching_rule(cleaned)
        if rule is not None:
            flags.append(SecurityFlag(location=location, rule=rule))
            return f"[content withheld: text resembling instructions to an AI ({rule})]"
        if len(cleaned) > self._max_cell_chars:
            return (
                cleaned[: self._max_cell_chars]
                + f"... [truncated, {len(cleaned)} characters in total]"
            )
        return cleaned

    def clean_value(self, value: Any, location: str, flags: list[SecurityFlag]) -> Any:
        """Clean a value of any JSON-friendly type; only strings (and strings nested in
        lists or dicts) are altered."""
        if isinstance(value, str):
            return self.clean_text(value, location, flags)
        if isinstance(value, list):
            return [self.clean_value(v, location, flags) for v in value]
        if isinstance(value, dict):
            return {
                self.clean_text(str(k), location, flags): self.clean_value(v, location, flags)
                for k, v in value.items()
            }
        return value


def _strip_invisible(text: str) -> str:
    """Drop control characters (except tab and newline) and Unicode 'format' characters such
    as zero-width spaces and right-to-left overrides, which can hide text from a human reader."""
    return "".join(
        ch
        for ch in text
        if ch in "\t\n" or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co")
    )


def _normalise(text: str) -> str:
    """A copy for pattern matching only: fullwidth and look-alike forms folded to plain
    ASCII where possible, and runs of whitespace collapsed."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text))


def _first_matching_rule(text: str) -> str | None:
    normalised = _normalise(text)
    for name, pattern in _RULES:
        if pattern.search(normalised):
            return name
    return None
