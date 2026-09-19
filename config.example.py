"""
SQL Server connection settings. Edit these to match your machine.

The two common setups:

  Windows, logging in with your Windows account (easiest):
      "trusted_connection": True         # ignores username/password below

  SQL Server auth with a username/password:
      "trusted_connection": False
      "username": "sa",
      "password": "your-password"

SERVER examples:
      "localhost\\SQLEXPRESS"   (default Express instance)
      "localhost,1433"          (default port)
      ".\\SQLEXPRESS"           (local Express, shorthand)
"""

SQLSERVER = {
    "driver": "ODBC Driver 18 for SQL Server",   # what you installed (see IMPORT_README.md)
    "server": "localhost",
    "database": "BudgetTracker",
    "trusted_connection": True,
    "username": "",
    "password": "",
}