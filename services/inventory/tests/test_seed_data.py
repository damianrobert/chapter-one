"""Sanity checks on the seed migration's data, without needing Postgres."""

import importlib.util
import uuid
from pathlib import Path

MIGRATION = Path(__file__).parent.parent / "migrations" / "versions" / "0002_seed_stock.py"

# Catalog's demo book ids (catalog migration 0002). Inventory can't read catalog's DB,
# so this test is what keeps the two lists in step.
CATALOG_SEED_IDS = {uuid.UUID(f"11111111-1111-4111-8111-00000000000{n}") for n in range(1, 7)}


def load_seed_stock() -> list[dict]:
    # Migration filenames start with a digit, so they can't be imported normally.
    spec = importlib.util.spec_from_file_location("seed_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SEED_STOCK


def test_seed_covers_exactly_the_catalog_demo_books():
    ids = [row["book_id"] for row in load_seed_stock()]

    assert len(ids) == len(set(ids))  # no duplicates
    assert set(ids) == CATALOG_SEED_IDS


def test_seed_quantities_are_non_negative_integers():
    for row in load_seed_stock():
        # bool is a subclass of int in Python, so exclude it explicitly.
        assert type(row["quantity"]) is int
        assert row["quantity"] >= 0
