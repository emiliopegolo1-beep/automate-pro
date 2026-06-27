#!/usr/bin/env python3
"""
Migrate data from SQLite (leads.db) to PostgreSQL.

Usage:
  export DATABASE_URL=postgresql://user:pass@host:5432/dbname
  python migrate_data.py

This script reads all rows from the local SQLite leads.db and inserts
them into the PostgreSQL database specified by DATABASE_URL.
Existing leads with the same ID are skipped (ON CONFLICT DO NOTHING).
"""
import os
import sqlite3
import psycopg2
import psycopg2.extras

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not os.path.exists(DB_PATH):
    print(f"SQLite database not found at {DB_PATH}")
    print("Nothing to migrate.")
    exit(0)

if not DATABASE_URL:
    print("DATABASE_URL environment variable not set.")
    print("Set it to your PostgreSQL connection string and try again.")
    exit(1)

print(f"Reading from: {DB_PATH}")
print("Connecting to PostgreSQL...")

# Connect to SQLite
sqlite_conn = sqlite3.connect(DB_PATH)
sqlite_conn.row_factory = sqlite3.Row

# Connect to PostgreSQL
pg_conn = psycopg2.connect(DATABASE_URL)
pg_cur = pg_conn.cursor()

# Ensure tables exist (run init schema)
from server import init_db
init_db()

# ——— Migrate leads ———
print("\nMigrating leads...")
sqlite_cur = sqlite_conn.cursor()
sqlite_cur.execute("SELECT * FROM leads")
leads = sqlite_cur.fetchall()

lead_columns = [
    "id", "name", "email", "business_type", "message", "phone",
    "status", "notes", "revenue", "auto_responded", "notified",
    "created_at", "updated_at", "requirements", "quoted_price",
    "follow_up_date", "source", "notify_email"
]

migrated_leads = 0
for row in leads:
    d = dict(row)
    values = tuple(d.get(col, None) for col in lead_columns)
    placeholders = ", ".join(["%s"] * len(lead_columns))
    cols = ", ".join(lead_columns)
    pg_cur.execute(
        f"INSERT INTO leads ({cols}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
        values,
    )
    if pg_cur.rowcount:
        migrated_leads += 1

pg_conn.commit()
print(f"  Leads: {migrated_leads} migrated ({len(leads)} total in SQLite)")

# ——— Migrate payments ———
print("\nMigrating payments...")
sqlite_cur.execute("SELECT * FROM payments")
payments = sqlite_cur.fetchall()

payment_columns = [
    "id", "lead_id", "stripe_session_id", "amount", "currency",
    "plan_name", "payment_type", "status", "customer_email", "created_at"
]

migrated_payments = 0
for row in payments:
    d = dict(row)
    values = tuple(d.get(col, None) for col in payment_columns)
    placeholders = ", ".join(["%s"] * len(payment_columns))
    cols = ", ".join(payment_columns)
    pg_cur.execute(
        f"INSERT INTO payments ({cols}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
        values,
    )
    if pg_cur.rowcount:
        migrated_payments += 1

pg_conn.commit()
print(f"  Payments: {migrated_payments} migrated ({len(payments)} total in SQLite)")

# ——— Migrate invoices ———
print("\nMigrating invoices...")
sqlite_cur.execute("SELECT * FROM invoices")
invoices = sqlite_cur.fetchall()

invoice_columns = [
    "id", "lead_id", "client_name", "client_email", "amount",
    "description", "status", "due_date", "invoice_number",
    "created_at", "paid_at", "has_subscription", "sub_amount",
    "sub_interval", "sub_description", "stripe_subscription_id", "sub_status"
]

migrated_invoices = 0
for row in invoices:
    d = dict(row)
    values = tuple(d.get(col, None) for col in invoice_columns)
    placeholders = ", ".join(["%s"] * len(invoice_columns))
    cols = ", ".join(invoice_columns)
    pg_cur.execute(
        f"INSERT INTO invoices ({cols}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
        values,
    )
    if pg_cur.rowcount:
        migrated_invoices += 1

pg_conn.commit()
print(f"  Invoices: {migrated_invoices} migrated ({len(invoices)} total in SQLite)")

# Cleanup
sqlite_conn.close()
pg_cur.close()
pg_conn.close()

print(f"\nMigration complete. {migrated_leads} leads, {migrated_payments} payments, {migrated_invoices} invoices migrated.")
