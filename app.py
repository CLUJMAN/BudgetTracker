"""
Flask web dashboard for the budget tracker.

Reads the same SQL Server database the importer writes to (through
db.get_connection) and shows it in the browser: account balances, this month's
income vs. spending, spending by category, and recent transactions.

Run it:
    python app.py
Then open http://127.0.0.1:5000 in your browser.

Notes:
- The SQL here is deliberately portable (plain SELECT/JOIN/GROUP BY). Anything
  date-specific -- "this month", top-N -- is done in Python, so the exact same
  queries run on SQL Server and on the local test database.
- get_db() imports the DB driver lazily, so this module can be loaded and tested
  without pyodbc installed.
"""
from datetime import date

from flask import Flask, render_template

app = Flask(__name__)


def get_db():
    from db import get_connection      # lazy import: no driver needed just to load this file
    return get_connection()


def query(sql, params=()):
    """Run a SELECT and return the rows as a list of plain dicts."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _money(value):
    """pyodbc returns Decimal, SQLite returns float -- normalize to float."""
    return float(value) if value is not None else 0.0


@app.template_filter("money")
def money_filter(value):
    v = _money(value)
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


ALL_TXNS_SQL = """
    SELECT t.id, t.txn_date, t.description, t.amount,
           acc.name AS account_name,
           c.name   AS category_name
    FROM transactions t
    JOIN accounts acc ON acc.id = t.account_id
    LEFT JOIN categories c ON c.id = t.category_id
    ORDER BY t.txn_date DESC, t.id DESC
"""


def _clean_txns(rows):
    for t in rows:
        t["amount"] = _money(t["amount"])
        t["txn_date"] = str(t["txn_date"])[:10]
        t["category_name"] = t["category_name"] or "Uncategorized"
    return rows


@app.route("/")
def dashboard():
    # Account balances: starting balance plus the signed sum of its transactions.
    accounts = query(
        """
        SELECT a.id, a.name, a.type, a.last_four, a.starting_balance,
               a.starting_balance + COALESCE(SUM(t.amount), 0) AS balance
        FROM accounts a
        LEFT JOIN transactions t ON t.account_id = a.id
        GROUP BY a.id, a.name, a.type, a.last_four, a.starting_balance
        ORDER BY a.name
        """
    )
    for a in accounts:
        a["balance"] = _money(a["balance"])
    net_worth = sum(a["balance"] for a in accounts)

    txns = _clean_txns(query(ALL_TXNS_SQL))

    # "This month" and its breakdowns, computed in Python so the SQL stays portable.
    current_month = date.today().strftime("%Y-%m")
    month_txns = [t for t in txns if t["txn_date"][:7] == current_month]
    month_income = sum(t["amount"] for t in month_txns if t["amount"] > 0)
    month_expense = -sum(t["amount"] for t in month_txns if t["amount"] < 0)

    cat_totals = {}
    for t in month_txns:
        if t["amount"] < 0:
            cat_totals[t["category_name"]] = cat_totals.get(t["category_name"], 0.0) - t["amount"]
    spending = sorted(
        ({"name": k, "total": v} for k, v in cat_totals.items()),
        key=lambda s: s["total"], reverse=True,
    )
    largest = max((s["total"] for s in spending), default=0.0)
    for s in spending:
        s["pct"] = (s["total"] / largest * 100) if largest else 0

    return render_template(
        "dashboard.html",
        accounts=accounts,
        net_worth=net_worth,
        month_label=date.today().strftime("%B %Y"),
        month_income=month_income,
        month_expense=month_expense,
        month_net=month_income - month_expense,
        spending=spending,
        recent=txns[:15],
    )


@app.route("/transactions")
def transactions():
    txns = _clean_txns(query(ALL_TXNS_SQL))
    return render_template("transactions.html", txns=txns)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
