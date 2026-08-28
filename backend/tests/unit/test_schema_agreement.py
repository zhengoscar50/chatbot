"""Do the columns we ask PostgREST for actually exist?

Written after `messages_for_sessions` selected `text` from a table whose column
is `content`. Every unit test passed, because the fakes were hand-written by
the same person as the query and agreed with it. The live route returned 502
on its first real request.

A fake cannot catch that: it only knows what its author told it. The migrations
are the one description of the schema in the repo that PostgREST also answers
to, so the queries are checked against those instead.
"""
import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
CLIENT = Path(__file__).resolve().parents[2] / "app" / "clients" / "powabase_client.py"


def columns_of(table: str) -> set:
    """Column names declared for `table` across every migration.

    Covers `create table` bodies and later `alter table ... add column`, since
    a column added by a later migration is just as real as an original one.
    """
    found: set = set()
    for sql in sorted(MIGRATIONS.glob("*.sql")):
        text = sql.read_text()

        for body in re.findall(
            rf"create table if not exists public\.{table}\s*\((.*?)\n\);",
            text, re.S | re.I,
        ):
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("--"):
                    continue
                name = line.split()[0].strip('",')
                if name.lower() in {"primary", "unique", "constraint", "foreign", "check"}:
                    continue
                found.add(name)

        for name in re.findall(
            rf"alter table public\.{table}\s+add column(?:\s+if not exists)?\s+(\w+)",
            text, re.I,
        ):
            found.add(name)
    return found


def selected_columns(method: str) -> set:
    """The `select` list a client method asks PostgREST for."""
    src = CLIENT.read_text()
    start = src.index(f"def {method}(")
    end = src.find("\n    def ", start)
    body = src[start:end if end != -1 else len(src)]
    match = re.search(r'"select":\s*"([^"]+)"', body)
    if not match:
        pytest.skip(f"{method} does not pin a select list")
    return {c.strip() for c in match.group(1).split(",") if c.strip()}


def test_the_migrations_are_readable():
    """Guards the guard: if the parsing silently found nothing, every check
    below would pass vacuously."""
    cols = columns_of("messages")

    assert {"session_id", "role", "content", "created_at"} <= cols, cols


def test_the_inbox_query_asks_only_for_columns_that_exist():
    """The exact failure this file exists for: `text` is not a column of
    messages, and asking for it is a 400 from PostgREST that surfaces as a 502
    to the user."""
    asked = selected_columns("messages_for_sessions")

    assert asked <= columns_of("messages"), asked - columns_of("messages")


def test_no_client_query_asks_for_a_column_the_messages_table_lacks():
    """Every select against `messages`, not just the one that broke."""
    src = CLIENT.read_text()
    real = columns_of("messages")
    for block in re.findall(r'"/rest/v1/messages".*?\)\n', src, re.S):
        match = re.search(r'"select":\s*"([^"]+)"', block)
        if not match:
            continue
        asked = {c.strip() for c in match.group(1).split(",") if c.strip()}
        assert asked <= real, f"unknown columns: {asked - real}"
