"""
Import Chase CSV exports into the budget_tracker database.

Usage:
    python chase_import.py path/to/Chase.CSV --account 1
    python chase_import.py "statements/*.csv" --account 1

Handles both Chase layouts automatically, detected by their headers:
  Checking:  Details, Posting Date, Description, Amount, Type, Balance, Check or Slip #
  Credit:    Transaction Date, Post Date, Description, Category, Type, Amount, Memo

Amounts are stored SIGNED, exactly as Chase exports them:
    negative = money out,  positive = money in.

Re-importing overlapping files is safe: a row already present for that account
(same date + amount + description) is skipped rather than duplicated.

The functions here take a live DB connection and use only '?' placeholders,
which are what both pyodbc (SQL Server) and the test harness speak, so the
same code path is what runs in production.
"""
import argparse
import csv
import glob
import os
import sys


# --- Chase format detection -------------------------------------------------

CHECKING_HEADERS = {"Details", "Posting Date", "Description", "Amount", "Type", "Balance"}
CREDIT_HEADERS = {"Transaction Date", "Post Date", "Description", "Category", "Type", "Amount"}


def detect_format(fieldnames):
    """Return 'checking' or 'credit' by looking at the CSV's header row."""
    cols = {c.strip() for c in (fieldnames or [])}
    if CHECKING_HEADERS.issubset(cols):
        return "checking"
    if CREDIT_HEADERS.issubset(cols):
        return "credit"
    raise ValueError(
        "Unrecognized CSV layout. Columns found: "
        + ", ".join(sorted(cols))
        + "\nExpected a Chase checking or credit-card export."
    )


# --- field parsing ----------------------------------------------------------

def parse_amount(raw):
    """'-54.20' or '$1,200.00' or '(30.00)' -> float, keeping the sign."""
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip().replace("$", "").replace(",", "")
    negative = s.startswith("(") and s.endswith(")")   # rare accounting style
    s = s.strip("()")
    value = float(s)
    return -value if negative else value


def parse_date(raw):
    """Chase dates are MM/DD/YYYY -> ISO 'YYYY-MM-DD' (accepted by SQL Server DATE)."""
    s = str(raw).strip()
    month, day, year = s.split("/")
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def normalize_row(row, fmt):
    """Map one Chase CSV row onto our schema's fields."""
    if fmt == "checking":
        return {
            "txn_date": parse_date(row["Posting Date"]),
            "description": (row.get("Description") or "").strip(),
            "amount": parse_amount(row["Amount"]),
            "category": None,                       # checking exports carry no category
        }
    # credit card
    category = (row.get("Category") or "").strip()
    return {
        "txn_date": parse_date(row["Transaction Date"]),
        "description": (row.get("Description") or "").strip(),
        "amount": parse_amount(row["Amount"]),
        "category": category or None,
    }


# --- category lookup (same SQL on SQL Server and the test DB) ----------------

def find_or_create_category(cur, name, kind):
    """Return the id for a category name, creating it if it's new."""
    if not name:
        return None
    cur.execute("SELECT id FROM categories WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO categories (name, kind) VALUES (?, ?)", (name, kind))
    cur.execute("SELECT id FROM categories WHERE name = ?", (name,))
    return cur.fetchone()[0]


# --- the import -------------------------------------------------------------

def _key(txn_date, amount, description):
    """Duplicate-detection key. Round money to cents to avoid float wobble."""
    return (str(txn_date), round(float(amount), 2), (description or "").strip())


def load_existing_keys(cur, account_id):
    """Keys for transactions already stored for this account, so re-imports skip them."""
    cur.execute(
        "SELECT txn_date, amount, description FROM transactions WHERE account_id = ?",
        (account_id,),
    )
    return {_key(d, a, desc) for d, a, desc in cur.fetchall()}


def import_csv(path, account_id, conn, categorize=True):
    """Import one Chase CSV file. Returns a small summary dict."""
    cur = conn.cursor()
    existing = load_existing_keys(cur, account_id)

    inserted = skipped = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fmt = detect_format(reader.fieldnames)

        for row in reader:
            item = normalize_row(row, fmt)

            # skip blank / unparseable rows
            if item["amount"] is None or not item["txn_date"]:
                skipped += 1
                continue

            key = _key(item["txn_date"], item["amount"], item["description"])
            if key in existing:
                skipped += 1
                continue

            category_id = None
            if categorize and item["category"]:
                kind = "income" if item["amount"] > 0 else "expense"
                category_id = find_or_create_category(cur, item["category"], kind)

            cur.execute(
                "INSERT INTO transactions "
                "(account_id, category_id, txn_date, description, amount) "
                "VALUES (?, ?, ?, ?, ?)",
                (account_id, category_id, item["txn_date"], item["description"], item["amount"]),
            )
            existing.add(key)
            inserted += 1

    conn.commit()
    return {"file": os.path.basename(path), "format": fmt,
            "inserted": inserted, "skipped": skipped}


# --- command line -----------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Import Chase CSV export(s) into budget_tracker.")
    ap.add_argument("csv", nargs="+", help="Path(s) to Chase CSV file(s). Globs like '*.csv' work.")
    ap.add_argument("--account", type=int, required=True,
                    help="The accounts.id to import these transactions into.")
    ap.add_argument("--no-categorize", action="store_true",
                    help="Don't auto-create categories from the card 'Category' column.")
    args = ap.parse_args()

    paths = []
    for pattern in args.csv:
        paths.extend(sorted(glob.glob(pattern)))
    if not paths:
        print("No files matched.", file=sys.stderr)
        sys.exit(1)

    from db import get_connection            # imported here so tests don't need the DB driver
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM accounts WHERE id = ?", (args.account,))
        acct = cur.fetchone()
        if not acct:
            print(f"No account with id={args.account}. Add it to the accounts table first.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"Importing into account #{args.account} ({acct[0]})\n")

        total_in = total_skip = 0
        for p in paths:
            r = import_csv(p, args.account, conn, categorize=not args.no_categorize)
            print(f"  {r['file']:<34} [{r['format']:<8}]  "
                  f"inserted {r['inserted']:>4}   skipped {r['skipped']:>4}")
            total_in += r["inserted"]
            total_skip += r["skipped"]

        print(f"\nDone. {total_in} inserted, {total_skip} skipped (duplicate or blank).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()