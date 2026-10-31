"""Sanity checks on the seed migration's data.

The DB constraints would reject bad rows too, but only when the migration runs
against a real database. These tests catch mistakes (float price, typo in currency,
copy-pasted duplicate id) instantly, without Postgres.
"""

import importlib.util
import re
import uuid
from pathlib import Path

MIGRATION = Path(__file__).parent.parent / "migrations" / "versions" / "0002_seed_books.py"


def load_seed_books() -> list[dict]:
    # Migration filenames start with a digit, so they can't be imported normally.
    spec = importlib.util.spec_from_file_location("seed_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SEED_BOOKS


def test_seed_ids_are_unique_uuids():
    ids = [book["id"] for book in load_seed_books()]

    assert all(isinstance(i, uuid.UUID) for i in ids)
    assert len(ids) == len(set(ids))


def test_seed_prices_are_non_negative_integers():
    for book in load_seed_books():
        # bool is a subclass of int in Python, so exclude it explicitly.
        assert type(book["price_minor"]) is int, book["title"]
        assert book["price_minor"] >= 0, book["title"]


def test_seed_currencies_are_iso_codes():
    for book in load_seed_books():
        assert re.fullmatch(r"[A-Z]{3}", book["currency"]), book["title"]
