"""Personal Budget Manager — Flask API + SQLite database."""
import os
import sqlite3
from datetime import datetime

from flask import Flask, g, jsonify, render_template, request

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "budget.db")

app = Flask(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL CHECK (type IN ('income', 'expense'))
);
CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL CHECK (type IN ('income', 'expense')),
    amount      REAL NOT NULL CHECK (amount > 0),
    category_id INTEGER NOT NULL REFERENCES categories (id),
    description TEXT NOT NULL DEFAULT '',
    date        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL UNIQUE REFERENCES categories (id) ON DELETE CASCADE,
    amount      REAL NOT NULL CHECK (amount > 0)
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions (date);
"""

DEFAULT_CATEGORIES = [
    ("Salary", "income"), ("Freelance", "income"), ("Investments", "income"), ("Other Income", "income"),
    ("Housing", "expense"), ("Groceries", "expense"), ("Dining Out", "expense"),
    ("Transportation", "expense"), ("Utilities", "expense"), ("Entertainment", "expense"),
    ("Healthcare", "expense"), ("Shopping", "expense"), ("Other Expense", "expense"),
]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        db.executemany("INSERT INTO categories (name, type) VALUES (?, ?)", DEFAULT_CATEGORIES)
    db.commit()
    db.close()


def valid_date(s, fmt="%Y-%m-%d"):
    try:
        datetime.strptime(s, fmt)
        return True
    except (ValueError, TypeError):
        return False


# ------------------------------- UI --------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------- Categories ----------------------------

@app.route("/api/categories", methods=["GET"])
def list_categories():
    rows = get_db().execute("SELECT * FROM categories ORDER BY type DESC, name").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/categories", methods=["POST"])
def create_category():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    ctype = data.get("type")
    if not name or ctype not in ("income", "expense"):
        return jsonify({"error": "A name and valid type are required"}), 400
    db = get_db()
    try:
        cur = db.execute("INSERT INTO categories (name, type) VALUES (?, ?)", (name, ctype))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "That category already exists"}), 409
    row = db.execute("SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/categories/<int:cid>", methods=["DELETE"])
def delete_category(cid):
    db = get_db()
    used = db.execute("SELECT COUNT(*) FROM transactions WHERE category_id = ?", (cid,)).fetchone()[0]
    if used:
        return jsonify({"error": "Category is used by transactions and cannot be deleted"}), 409
    db.execute("DELETE FROM budgets WHERE category_id = ?", (cid,))
    cur = db.execute("DELETE FROM categories WHERE id = ?", (cid,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Not found"}), 404
    return "", 204


# --------------------------- Transactions ---------------------------

@app.route("/api/transactions", methods=["GET"])
def list_transactions():
    month = request.args.get("month")
    query = """
        SELECT t.*, c.name AS category_name, c.type AS category_type
        FROM transactions t JOIN categories c ON c.id = t.category_id
    """
    params = []
    if month:
        query += " WHERE strftime('%Y-%m', t.date) = ?"
        params.append(month)
    query += " ORDER BY t.date DESC, t.id DESC"
    rows = get_db().execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/transactions", methods=["POST"])
def create_transaction():
    data = request.get_json(force=True)
    ttype = data.get("type")
    if ttype not in ("income", "expense"):
        return jsonify({"error": "type must be 'income' or 'expense'"}), 400
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid amount is required"}), 400
    if amount <= 0:
        return jsonify({"error": "Amount must be greater than zero"}), 400
    date = (data.get("date") or "").strip()
    if not valid_date(date):
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    description = (data.get("description") or "").strip()
    category_id = data.get("category_id")

    db = get_db()
    cat = db.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    if cat is None:
        return jsonify({"error": "Category not found"}), 400
    if cat["type"] != ttype:
        return jsonify({"error": "Category type does not match transaction type"}), 400

    cur = db.execute(
        "INSERT INTO transactions (type, amount, category_id, description, date) VALUES (?, ?, ?, ?, ?)",
        (ttype, amount, category_id, description, date),
    )
    db.commit()
    row = db.execute(
        """SELECT t.*, c.name AS category_name FROM transactions t
           JOIN categories c ON c.id = t.category_id WHERE t.id = ?""",
        (cur.lastrowid,),
    ).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/transactions/<int:tid>", methods=["DELETE"])
def delete_transaction(tid):
    db = get_db()
    cur = db.execute("DELETE FROM transactions WHERE id = ?", (tid,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Not found"}), 404
    return "", 204


# ------------------------------ Budgets -----------------------------

@app.route("/api/budgets", methods=["GET"])
def list_budgets():
    rows = get_db().execute(
        """SELECT b.id, b.category_id, b.amount, c.name AS category_name
           FROM budgets b JOIN categories c ON c.id = b.category_id ORDER BY c.name"""
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/budgets", methods=["POST"])
def upsert_budget():
    data = request.get_json(force=True)
    category_id = data.get("category_id")
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"error": "A valid amount is required"}), 400
    if amount <= 0:
        return jsonify({"error": "Amount must be greater than zero"}), 400
    db = get_db()
    cat = db.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    if cat is None:
        return jsonify({"error": "Category not found"}), 400
    if cat["type"] != "expense":
        return jsonify({"error": "Budgets apply to expense categories only"}), 400
    db.execute(
        """INSERT INTO budgets (category_id, amount) VALUES (?, ?)
           ON CONFLICT(category_id) DO UPDATE SET amount = excluded.amount""",
        (category_id, amount),
    )
    db.commit()
    row = db.execute(
        """SELECT b.id, b.category_id, b.amount, c.name AS category_name
           FROM budgets b JOIN categories c ON c.id = b.category_id WHERE b.category_id = ?""",
        (category_id,),
    ).fetchone()
    return jsonify(dict(row))


@app.route("/api/budgets/<int:category_id>", methods=["DELETE"])
def delete_budget(category_id):
    db = get_db()
    db.execute("DELETE FROM budgets WHERE category_id = ?", (category_id,))
    db.commit()
    return "", 204


# ------------------------------ Summary -----------------------------

@app.route("/api/summary", methods=["GET"])
def summary():
    month = request.args.get("month") or datetime.now().strftime("%Y-%m")
    if not valid_date(month, "%Y-%m"):
        return jsonify({"error": "month must be YYYY-MM"}), 400
    db = get_db()

    totals = db.execute(
        """SELECT type, COALESCE(SUM(amount), 0) AS total FROM transactions
           WHERE strftime('%Y-%m', date) = ? GROUP BY type""", (month,)).fetchall()
    income = sum(r["total"] for r in totals if r["type"] == "income")
    expenses = sum(r["total"] for r in totals if r["type"] == "expense")

    by_cat = db.execute(
        """SELECT c.id AS category_id, c.name, c.type, SUM(t.amount) AS total
           FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE strftime('%Y-%m', t.date) = ? GROUP BY c.id ORDER BY total DESC""",
        (month,)).fetchall()

    budgets = db.execute(
        """SELECT b.category_id, b.amount, c.name FROM budgets b
           JOIN categories c ON c.id = b.category_id ORDER BY c.name""").fetchall()

    spent_rows = db.execute(
        """SELECT category_id, COALESCE(SUM(amount), 0) AS spent FROM transactions
           WHERE type = 'expense' AND strftime('%Y-%m', date) = ? GROUP BY category_id""",
        (month,)).fetchall()
    spent_map = {r["category_id"]: r["spent"] for r in spent_rows}

    budget_status = []
    for b in budgets:
        spent = round(spent_map.get(b["category_id"], 0.0), 2)
        pct = (spent / b["amount"] * 100) if b["amount"] > 0 else 0
        budget_status.append({
            "category_id": b["category_id"], "category_name": b["name"],
            "budget": b["amount"], "spent": spent,
            "remaining": round(b["amount"] - spent, 2), "pct": round(pct, 1),
        })

    return jsonify({
        "month": month, "income": income, "expenses": expenses,
        "balance": round(income - expenses, 2),
        "by_category": [dict(r) for r in by_cat],
        "budgets": budget_status,
        "total_budget": sum(b["amount"] for b in budgets),
    })


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000)
