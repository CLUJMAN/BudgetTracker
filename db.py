"""
Opens a connection to your SQL Server budget_tracker database.

The rest of the app (and the Chase importer) only ever calls get_connection(),
so if you switch machines or auth methods, config.py is the only thing to change.
"""
import pyodbc
from config import SQLSERVER


def get_connection():
    parts = [
        f"DRIVER={{{SQLSERVER['driver']}}}",
        f"SERVER={SQLSERVER['server']}",
        f"DATABASE={SQLSERVER['database']}",
    ]
    if SQLSERVER.get("trusted_connection"):
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={SQLSERVER['username']}")
        parts.append(f"PWD={SQLSERVER['password']}")
    # Local dev servers use a self-signed cert; this avoids a TLS trust error.
    parts.append("TrustServerCertificate=yes")

    return pyodbc.connect(";".join(parts) + ";")


if __name__ == "__main__":
    # Quick connectivity check: python db.py
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT @@VERSION")
    print("Connected. Server version:\n", cur.fetchone()[0])
    conn.close()