#!/usr/bin/env python3
"""Automate Pro — Lead Capture & Admin Dashboard Server."""
import os
import sys
import psycopg2
import psycopg2.extras
import uuid
import functools
from datetime import datetime, date, timedelta

from dotenv import load_dotenv
load_dotenv()
import stripe

from flask import (
    Flask,
    request,
    jsonify,
    session,
    redirect,
    url_for,
    render_template_string,
    send_from_directory,
)

# Email integration — Gmail API (uses stored refresh token, no SMTP needed)
import base64
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.mime.text import MIMEText

GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
GMAIL_CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")

def _get_gmail_service():
    if not GMAIL_REFRESH_TOKEN:
        return None
    creds = Credentials(
        token=None,
        refresh_token=GMAIL_REFRESH_TOKEN,
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send"]
    )
    return build("gmail", "v1", credentials=creds)

def send_email(to, subject, body):
    """Send email via Gmail API using stored refresh token."""
    service = _get_gmail_service()
    if not service:
        print("[EMAIL DISABLED] Set GMAIL_REFRESH_TOKEN env var to enable")
        return {"success": False, "error": "Gmail not configured"}
    try:
        msg = MIMEText(body)
        msg["To"] = to
        msg["Subject"] = subject
        msg["From"] = "me"
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {"success": True}
    except Exception as e:
        print(f"[GMAIL ERROR] {e}")
        return {"success": False, "error": str(e)}

def send_email_sync(to, subject, body):
    """Send email via SMTP. Works on Railway with app password."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"[EMAIL DISABLED] Set SMTP_USER and SMTP_PASS env vars to enable")
        return {"success": False, "error": "SMTP not configured"}
    try:
        msg = MIMEText(body)
        msg["To"] = to
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [to], msg.as_string())
        server.quit()
        return {"success": True}
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return {"success": False, "error": str(e)}

app = Flask(__name__)
app.secret_key = os.urandom(32).hex()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "emilio.pegolo1@gmail.com")
DASHBOARD_PASSWORD = "automate2026"
DOMAIN = os.environ.get("DOMAIN", "automate-pro-production.up.railway.app")

CLIENT_CREDENTIALS = {
    "bob": {"name": "Bob's Plumbing", "password": "plumb2026", "notify_email": "bob@bobsplumbing.com"},
}

# ── Stripe Configuration ─────────────────────────────────────────────────────

STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "pk_test_PLACEHOLDER")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_PLACEHOLDER")

# In-memory cache for Stripe price IDs (key → price_id)
PRICE_CACHE = {}

PLANS = {
    "starter_setup": {"name": "Automate Pro - Starter Setup", "amount": 49700, "type": "one_time"},
    "starter_monthly": {"name": "Automate Pro - Starter Monthly", "amount": 19700, "type": "recurring"},
    "growth_setup": {"name": "Automate Pro - Growth Setup", "amount": 99700, "type": "one_time"},
    "growth_monthly": {"name": "Automate Pro - Growth Monthly", "amount": 49700, "type": "recurring"},
    "scale_setup": {"name": "Automate Pro - Scale Setup", "amount": 199700, "type": "one_time"},
    "scale_monthly": {"name": "Automate Pro - Scale Monthly", "amount": 99700, "type": "recurring"},
}


def create_test_products():
    """Create test products/prices in Stripe if not already cached."""
    if PRICE_CACHE:
        return
    for key, plan in PLANS.items():
        try:
            # Create or find the product
            product = stripe.Product.create(name=plan["name"])
            price_data = {
                "product": product.id,
                "unit_amount": plan["amount"],
                "currency": "usd",
            }
            if plan["type"] == "recurring":
                price_data["recurring"] = {"interval": "month"}
            price = stripe.Price.create(**price_data)
            PRICE_CACHE[key] = price.id
            print(f"  [Stripe] Created {plan['name']}: {price.id}")
        except Exception as e:
            print(f"  [Stripe] Error creating {plan['name']}: {e}")


# ── Helpers ──────────────────────────────────────────────────────────────────


def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable not set")
    dsn = DATABASE_URL.strip()
    if "sslmode" not in dsn and "postgresql" in dsn:
        separator = "?" if "?" not in dsn else "&"
        dsn = f"{dsn}{separator}sslmode=require"
    conn = psycopg2.connect(dsn)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def init_db():
    """Create tables if they don't exist. Safe to run multiple times."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            business_type TEXT,
            message TEXT,
            phone TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            notes TEXT DEFAULT '',
            revenue REAL DEFAULT 0,
            auto_responded INTEGER DEFAULT 0,
            notified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            requirements TEXT DEFAULT '',
            quoted_price REAL DEFAULT 0,
            follow_up_date TEXT DEFAULT '',
            source TEXT DEFAULT 'automate_pro',
            notify_email TEXT DEFAULT ''
        )
    """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            lead_id TEXT,
            stripe_session_id TEXT,
            amount REAL,
            currency TEXT DEFAULT 'usd',
            plan_name TEXT,
            payment_type TEXT,
            status TEXT,
            customer_email TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS invoices (
            id TEXT PRIMARY KEY,
            lead_id TEXT,
            client_name TEXT NOT NULL,
            client_email TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            due_date TEXT,
            invoice_number TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT NOW(),
            paid_at TIMESTAMP,
            has_subscription INTEGER DEFAULT 0,
            sub_amount REAL DEFAULT 0,
            sub_interval TEXT DEFAULT 'month',
            sub_description TEXT DEFAULT '',
            stripe_subscription_id TEXT DEFAULT '',
            sub_status TEXT DEFAULT 'none'
        )
    """
    )

    conn.commit()
    cur.close()
    conn.close()


def _execute(conn, sql, params=None):
    """Run a SQL statement and return the cursor. Auto-commits for INSERT/UPDATE/DELETE."""
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    return cur


def save_lead(lead_id, name, email, business_type, message, phone="", source="", notify_email=""):
    conn = get_db()
    cur = conn.cursor()
    if not source:
        source = "automate_pro"
    _execute(conn,
        "INSERT INTO leads (id, name, email, business_type, message, phone, source, notify_email) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (lead_id, name, email, business_type, message, phone, source, notify_email),
    )
    conn.close()


def get_lead_by_id(lead_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM leads WHERE id = %s", (lead_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_lead_in_db(lead_id, updates):
    """Update lead columns. `updates` is a dict of column → value."""
    allowed = {"name", "email", "business_type", "phone", "status", "notes", "revenue", "requirements", "quoted_price", "follow_up_date", "source", "notify_email"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return False
    filtered["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = %s" for k in filtered)
    values = list(filtered.values()) + [lead_id]
    conn = get_db()
    cur = conn.cursor()
    _execute(conn, f"UPDATE leads SET {set_clause} WHERE id = %s", values)
    conn.close()
    return True


def mark_auto_responded(lead_id):
    conn = get_db()
    cur = conn.cursor()
    _execute(conn, "UPDATE leads SET auto_responded = 1 WHERE id = %s", (lead_id,))
    conn.close()


def mark_notified(lead_id):
    conn = get_db()
    cur = conn.cursor()
    _execute(conn, "UPDATE leads SET notified = 1 WHERE id = %s", (lead_id,))
    conn.close()


def build_auto_reply_body(name, business_type):
    return (
        f"Hi {name},\n\n"
        f"Thanks for reaching out about automating your {business_type} business!\n\n"
        "We specialize in building custom AI workflows that handle your leads, "
        "bookings, follow-ups, and admin — so you can focus on the work that pays.\n\n"
        "Next step: Pick a time for a free 15-minute discovery call.\n"
        f"Book here: https://calendly.com/emilio-pegolo1/30min\n\n"
        "Looking forward to connecting,\n\n"
        "Emilio\n"
        "Automate Pro"
    )


def build_notify_body(name, email, business_type, message, timestamp):
    return (
        "New Lead Captured!\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Business: {business_type}\n"
        f"Message: {message or '(none)'}\n"
        f"Time: {timestamp}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Auto-response sent: Yes"
    )


# ── Invoice Helpers ──────────────────────────────────────────────────────────

import random
import string

def generate_invoice_number():
    """Generate next sequential invoice number (INV-0001 format)."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT invoice_number FROM invoices ORDER BY invoice_number DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if row and row["invoice_number"]:
        try:
            num = int(row["invoice_number"].replace("INV-", ""))
            return f"INV-{num + 1:04d}"
        except ValueError:
            pass
    return "INV-0001"


def get_invoice_by_id(invoice_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def build_invoice_email_body(inv, stripe_url=None, sub_stripe_url=None):
    payment_section = f"\nPay setup fee: {stripe_url}\n" if stripe_url else f"\nView invoice: https://automate-pro-production.up.railway.app/invoice/{inv['id']}\n"
    sub_section = ""
    if inv.get("has_subscription") and sub_stripe_url:
        sub_amt = inv.get("sub_amount", 0)
        sub_int = inv.get("sub_interval", "month")
        sub_desc = inv.get("sub_description", "") or "Monthly retainer"
        sub_section = f"\nSubscription: {sub_desc} — ${sub_amt:.2f}/{sub_int}\nSubscribe: {sub_stripe_url}\n"
    return (
        f"Hi {inv['client_name']},\n\n"
        f"Your invoice #{inv['invoice_number']} is ready.\n"
        f"Setup fee: ${inv['amount']:.2f}\n"
        f"Description: {inv.get('description') or 'Automation Services'}\n".rstrip() + "\n"
        f"Due: {inv.get('due_date') or 'Upon receipt'}\n"
        f"{payment_section}"
        f"{sub_section}"
        "\n"
        "Thanks,\n"
        "Emilio Pegolo\n"
        "Automate Pro"
    )


# ── Auth decorator ───────────────────────────────────────────────────────────


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)

    return wrapper


# ── Lead Capture (existing, unchanged) ───────────────────────────────────────



# ── React SPA (new site) ──────────────────────────────────────────────────────

@app.route("/")
def serve_index():
    return serve_react("index.html")

@app.route("/services")
def serve_services():
    return serve_react("index.html")

@app.route("/about")
def serve_about():
    return serve_react("index.html")

@app.route("/contact")
def serve_contact():
    return serve_react("index.html")

@app.route("/assets/<path:filename>")
def serve_react_assets(filename):
    return serve_react("assets/" + filename)

@app.route("/favicon.svg")
def serve_react_favicon():
    return serve_react("favicon.svg")

@app.route("/icons.svg")
def serve_react_icons():
    return serve_react("icons.svg")

REACT_DIST = os.path.join(os.path.dirname(__file__), "react-dist")

def serve_react(path):
    full = os.path.join(REACT_DIST, path)
    if os.path.isfile(full):
        resp = send_from_directory(REACT_DIST, path)
        resp.headers["Cache-Control"] = "no-store, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp
    return "File not found", 404





# ── AI Chat (DeepSeek) ──────────────────────────────────────────────────────────

import urllib.request
import json

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return jsonify({"error": "Message required"}), 400

    user_msg = data["message"][:2000]
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    if not api_key:
        return jsonify({"reply": "AI chat is not configured yet. Drop us a message and we'll get back to you!"})

    try:
        body = json.dumps({
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are Automate Pro's AI assistant. You help local business owners understand how AI automation can save them time. Be helpful, concise, and friendly. Your goal is to answer questions about services and schedule a strategy call."},
                {"role": "user", "content": user_msg}
            ],
            "max_tokens": 500,
            "temperature": 0.7
        }).encode()

        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            reply = result["choices"][0]["message"]["content"]
            return jsonify({"reply": reply})

    except Exception as e:
        return jsonify({"reply": "Sorry, I'm having trouble connecting. Please email us at hello@automatepro.ai!"})


@app.route("/api/lead", methods=["POST"])
def api_lead():
    import traceback as _tb
    try:
        return _api_lead_impl()
    except Exception as _e:
        return jsonify({"error": str(_e), "traceback": _tb.format_exc()}), 500

def _api_lead_impl():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    business_type = (data.get("business_type") or "").strip()
    message = (data.get("message") or "").strip()
    raw_notify_email = (data.get("notify_email") or "").strip()

    if not name or not email:
        return jsonify({"error": "Name and email are required"}), 400

    # Determine source and resolve client
    notify_email = ""
    source = "automate_pro"
    matched_client_id = None
    if raw_notify_email:
        for client_id, creds in CLIENT_CREDENTIALS.items():
            if creds["notify_email"] == raw_notify_email:
                source = f"client:{client_id}"
                notify_email = raw_notify_email
                matched_client_id = client_id
                break

    lead_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    save_lead(lead_id, name, email, business_type, message, phone, source, notify_email)

    gmail_errors = []

    # Auto-reply to the lead
    auto_subject = f"Thanks for reaching out, {name}!"
    auto_body = build_auto_reply_body(name, business_type or "your")
    result = send_email(email, auto_subject, auto_body)
    if result.get("success"):
        mark_auto_responded(lead_id)
    else:
        gmail_errors.append(f"Auto-reply failed: {result.get('error')}")

    # Notify Emilio
    notify_subject = f"\U0001f680 New Lead: {name} - {business_type or 'N/A'}"
    notify_body = build_notify_body(name, email, business_type, message, timestamp)
    if source.startswith("client:"):
        notify_body += "\nSource: Client Portal " + source
    result = send_email(NOTIFY_EMAIL, notify_subject, notify_body)
    if result.get("success"):
        mark_notified(lead_id)
    else:
        gmail_errors.append(f"Notify failed: {result.get('error')}")

    # Notify the client if this is a client lead
    if matched_client_id and notify_email:
        client_creds = CLIENT_CREDENTIALS[matched_client_id]
        client_notify_subject = f"\U0001f4ac New Lead: {name} - {business_type or 'Inquiry'}"
        client_notify_body = (
            f"Hi {client_creds['name']},\n\n"
            f"You have a new lead from your website:\n\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Phone: {phone or '(not provided)'}\n"
            f"Service: {business_type or 'N/A'}\n"
            f"Message: {message or '(none)'}\n"
            f"Time: {timestamp}\n\n"
            f"View all leads: https://{DOMAIN}/portal/{matched_client_id}\n"
        )
        result = send_email(notify_email, client_notify_subject, client_notify_body)
        if not result.get("success"):
            gmail_errors.append(f"Client notify failed: {result.get('error')}")

    response = {"success": True, "lead_id": lead_id, "name": name}
    if gmail_errors:
        response["gmail_warnings"] = gmail_errors

    return jsonify(response), 201


@app.route("/api/leads", methods=["GET"])
def api_leads():
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Dashboard API Endpoints ──────────────────────────────────────────────────


@app.route("/api/dashboard", methods=["GET"])
@login_required
def api_dashboard():
    conn = get_db()
    cur = conn.cursor()
    include_all = request.args.get("include_all", "false").lower() == "true"

    source_filter = ""
    if not include_all:
        source_filter = "WHERE source = 'automate_pro'"

    # Total leads
    status_rows = cur.execute(
        f"SELECT status, COUNT(*) as cnt FROM leads {source_filter} GROUP BY status"
    ).fetchall()
    leads_by_status = {r["status"]: r["cnt"] for r in status_rows}
    for s in ("new", "call_scheduled", "call_done", "building", "demo_ready", "delivered", "paid"):
        leads_by_status.setdefault(s, 0)

    # Revenue totals (from payments table)
    total_revenue = cur.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'completed'"
    ).fetchone()[0]

    # Revenue this month
    first_of_month = date.today().replace(day=1).isoformat()
    revenue_this_month = cur.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'completed' AND created_at >= %s",
        (first_of_month,),
    ).fetchone()[0]

    # New this week
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    source_clause_week = "" if include_all else "AND source = 'automate_pro'"
    new_this_week = cur.execute(
        f"SELECT COUNT(*) FROM leads WHERE created_at >= %s {source_clause_week}", (week_ago,)
    ).fetchone()[0]

    # Conversion rate (delivered+paid / touched leads)
    total_new = leads_by_status.get("new", 0)
    total_won = leads_by_status.get("delivered", 0) + leads_by_status.get("paid", 0)
    total_contacted = sum(
        leads_by_status.get(s, 0)
        for s in ("call_scheduled", "call_done", "building", "demo_ready", "delivered", "paid")
    )
    conversion_rate = 0
    denominator = total_new + total_contacted
    if denominator > 0:
        conversion_rate = round((total_won / denominator) * 100, 1)

    # Recent leads (last 5)
    recent = cur.execute(
        f"SELECT * FROM leads {source_filter} ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    conn.close()

    # Pending Delivery = building + demo_ready
    pending_delivery = leads_by_status.get("building", 0) + leads_by_status.get("demo_ready", 0)

    # Active Clients = delivered + paid
    active_clients = leads_by_status.get("delivered", 0) + leads_by_status.get("paid", 0)

    return jsonify(
        {
            "total_leads": total_leads,
            "leads_by_status": leads_by_status,
            "revenue_this_month": revenue_this_month,
            "total_revenue": total_revenue,
            "new_this_week": new_this_week,
            "conversion_rate": conversion_rate,
            "pending_delivery": pending_delivery,
            "active_clients": active_clients,
            "recent_leads": [dict(r) for r in recent],
            "filtering": not include_all,
        }
    )


@app.route("/api/lead/<lead_id>", methods=["PUT"])
@login_required
def api_update_lead(lead_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    update_lead_in_db(lead_id, data)
    updated = get_lead_by_id(lead_id)
    return jsonify({"success": True, "lead": updated})


@app.route("/api/lead/<lead_id>/send-email", methods=["POST"])
@login_required
def api_send_lead_email(lead_id):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    if not subject or not body:
        return jsonify({"error": "Subject and body are required"}), 400

    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    result = send_email(lead["email"], subject, body)
    if result.get("success"):
        return jsonify({"success": True, "message": f"Email sent to {lead['email']}"})
    else:
        return jsonify({"error": result.get("error", "Failed to send email")}), 500


@app.route("/api/lead/<lead_id>", methods=["GET"])
@login_required
def api_get_lead(lead_id):
    lead = get_lead_by_id(lead_id)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify(lead)


# ── Auth Pages ───────────────────────────────────────────────────────────────


@app.route("/login", methods=["GET"])
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("dashboard_page"))
    return render_template_string(LOGIN_HTML)


@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    if password == DASHBOARD_PASSWORD:
        session["logged_in"] = True
        return jsonify({"success": True})
    return jsonify({"error": "Invalid password"}), 401


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("logged_in", None)
    return jsonify({"success": True})


# ── Client Portal Routes ────────────────────────────────────────────────────


CLIENT_PORTAL_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{client_name}} — Lead Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #0a0a0f;
    --surface: #12121a;
    --surface-2: #1a1a26;
    --border: #2a2a3a;
    --text: #e0e0e0;
    --text-muted: #888;
    --accent: #ff8c42;
    --accent-hover: #e07a30;
    --green: #00d4aa;
  }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .login-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 48px 40px;
    width: 100%;
    max-width: 400px;
    text-align: center;
  }
  .login-card .brand-icon { font-size: 48px; margin-bottom: 12px; }
  .login-card h1 { font-size: 22px; margin-bottom: 4px; color: #fff; }
  .login-card .subtitle { color: var(--text-muted); font-size: 14px; margin-bottom: 28px; }
  .login-card input {
    width: 100%;
    padding: 14px 16px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: #fff;
    font-size: 16px;
    outline: none;
    transition: border-color 0.2s;
    margin-bottom: 16px;
  }
  .login-card input:focus { border-color: var(--accent); }
  .login-card input::placeholder { color: #666; }
  .login-card button {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 10px;
    background: var(--accent);
    color: #fff;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  .login-card button:hover { background: var(--accent-hover); }
  .login-card .error { color: #ff4d4d; font-size: 14px; margin-top: 12px; display: none; }
</style>
</head>
<body>
<div class="login-card">
  <div class="brand-icon">🔑</div>
  <h1>{{client_name}}</h1>
  <p class="subtitle">Enter your password to view your leads</p>
  <input type="password" id="password" placeholder="Portal Password" autocomplete="current-password">
  <button onclick="login()">Sign In</button>
  <div class="error" id="error"></div>
</div>
<script>
document.getElementById('password').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') login();
});
async function login() {
  const pwd = document.getElementById('password').value;
  const err = document.getElementById('error');
  if (!pwd) { err.textContent = 'Please enter your password'; err.style.display = 'block'; return; }
  try {
    const res = await fetch('/portal/{{client_id}}/login', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({password:pwd})
    });
    const data = await res.json();
    if (data.success) { window.location.href = '/portal/{{client_id}}'; }
    else { err.textContent = 'Invalid password'; err.style.display = 'block'; }
  } catch(e) { err.textContent = 'Connection error'; err.style.display = 'block'; }
}
</script>
</body>
</html>"""

CLIENT_PORTAL_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{client_name}} — Lead Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #0F172A;
    --surface: #1E293B;
    --surface-2: #334155;
    --border: rgba(255,255,255,0.08);
    --text: #F8FAFC;
    --text-muted: #94A3B8;
    --accent: #F59E0B;
    --accent-hover: #D97706;
    --green: #22C55E;
    --red: #EF4444;
    --blue: #3B82F6;
  }
  body {
    font-family: 'Fira Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 40px 24px;
  }
  .container { max-width: 1000px; margin: 0 auto; }
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 32px;
    padding-bottom: 20px;
    border-bottom: 1px solid var(--border);
  }
  .header-left { display: flex; align-items: center; gap: 12px; }
  .header-left .icon { color: var(--accent); display: flex; align-items: center; }
  .header-left h1 { font-size: 24px; font-weight: 700; color: #fff; }
  .header-left .sub { font-size: 13px; color: var(--text-muted); margin-top: 2px; }
  .header .logout-btn {
    padding: 10px 20px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: transparent;
    color: var(--text-muted);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    font-family: inherit;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .header .logout-btn:hover { color: var(--red); border-color: var(--red); }
  .stats-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 24px;
  }
  .stat-card .label { font-size: 13px; color: var(--text-muted); font-weight: 500; margin-bottom: 6px; }
  .stat-card .value { font-size: 32px; font-weight: 700; color: #fff; font-family: 'Fira Code', monospace; }
  .stat-card.accent .value { color: var(--accent); }
  .stat-card.green .value { color: var(--green); }
  .table-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  .data-table {
    width: 100%;
    border-collapse: collapse;
  }
  .data-table th {
    text-align: left;
    padding: 14px 20px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    font-weight: 600;
    border-bottom: 1px solid var(--border);
    background: var(--surface-2);
    font-family: 'Fira Code', monospace;
  }
  .data-table td {
    padding: 14px 20px;
    font-size: 14px;
    border-bottom: 1px solid var(--border);
    color: var(--text);
  }
  .data-table tr:last-child td { border-bottom: none; }
  .data-table tr:hover td { background: rgba(255,255,255,0.03); }
  .data-table .empty-state {
    text-align: center;
    padding: 48px 20px;
    color: var(--text-muted);
  }
  .data-table .empty-state .big-icon { margin-bottom: 12px; color: var(--text-muted); display: flex; justify-content: center; }
  .data-table .empty-state p { font-size: 15px; }
  .badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    text-transform: capitalize;
  }
  .badge-new { background: rgba(59,130,246,0.15); color: var(--blue); }
  .badge-contacted { background: rgba(245,158,11,0.15); color: var(--accent); }
  .badge-closed { background: rgba(34,197,94,0.15); color: var(--green); }
  .badge-default { background: rgba(148,163,184,0.15); color: var(--text-muted); }
  .footer {
    text-align: center;
    padding: 24px;
    font-size: 13px;
    color: var(--text-muted);
    margin-top: 32px;
    border-top: 1px solid var(--border);
  }
  .footer a { color: var(--accent); text-decoration: none; font-weight: 600; }
  @media (max-width: 600px) {
    .header { flex-direction: column; gap: 16px; text-align: center; }
    .stats-row { grid-template-columns: 1fr; }
    .data-table { font-size: 13px; }
    .data-table th, .data-table td { padding: 10px 12px; }
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="header-left">
      <div class="icon">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/>
        </svg>
      </div>
      <div>
        <h1>{{client_name}}</h1>
        <div class="sub">Lead Dashboard</div>
      </div>
    </div>
    <button class="logout-btn" onclick="logout()">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
      </svg>
      Sign Out
    </button>
  </div>

  <div class="stats-row">
    <div class="stat-card"><div class="label">Total Leads</div><div class="value" id="stat-total">—</div></div>
    <div class="stat-card green"><div class="label">This Month</div><div class="value" id="stat-month">—</div></div>
  </div>

  <div class="table-card">
    <table class="data-table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Phone</th>
          <th>Email</th>
          <th>Service</th>
          <th>Date</th>
        </tr>
      </thead>
      <tbody id="leads-body">
        <tr><td colspan="5" style="text-align:center;padding:40px;color:var(--text-muted);">Loading leads...</td></tr>
      </tbody>
    </table>
  </div>

  <div class="footer">
    Powered by <a href="/">Automate Pro</a>
  </div>
</div>

<script>
async function loadLeads() {
  const res = await fetch('/portal/{{client_id}}/api/leads');
  const data = await res.json();
  const tbody = document.getElementById('leads-body');

  if (!data.leads || data.leads.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">
      <div class="big-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg></div>
      <p>No leads yet. When someone contacts you from your website, they'll show up here.</p>
    </td></tr>`;
    document.getElementById('stat-total').textContent = '0';
    document.getElementById('stat-month').textContent = '0';
    return;
  }

  document.getElementById('stat-total').textContent = data.total;
  document.getElementById('stat-month').textContent = data.this_month;

  const now = new Date();
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  tbody.innerHTML = data.leads.map(lead => {
    let dateStr = '—';
    if (lead.created_at) {
      const d = new Date(lead.created_at);
      dateStr = d.getDate() + ' ' + months[d.getMonth()] + ' ' + d.getFullYear();
    }
    return `<tr>
      <td style="font-weight:600;">${escapeHtml(lead.name)}</td>
      <td>${escapeHtml(lead.phone || '—')}</td>
      <td><a href="mailto:${escapeHtml(lead.email)}" style="color:var(--accent);text-decoration:none;">${escapeHtml(lead.email)}</a></td>
      <td>${escapeHtml(lead.business_type || '—')}</td>
      <td style="color:var(--text-muted);font-size:13px;">${dateStr}</td>
    </tr>`;
  }).join('');
}

function escapeHtml(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function logout() {
  await fetch('/portal/{{client_id}}/logout', { method: 'POST' });
  window.location.href = '/portal/{{client_id}}';
}

loadLeads();
</script>
</body>
</html>"""


def get_client_portal_leads(client_id):
    source_prefix = f"client:{client_id}"
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT * FROM leads WHERE source = %s ORDER BY created_at DESC",
        (source_prefix,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.route("/portal/<client_id>", methods=["GET"])
def client_portal(client_id):
    if client_id not in CLIENT_CREDENTIALS:
        return "Portal not found", 404

    creds = CLIENT_CREDENTIALS[client_id]
    client_name = creds["name"]

    # Check if already logged in
    if session.get(f"portal_{client_id}"):
        leads = get_client_portal_leads(client_id)
        total = len(leads)
        now = datetime.now()
        this_month = sum(
            1 for l in leads
            if l.get("created_at") and l["created_at"][:7] == now.strftime("%Y-%m")
        )
        return render_template_string(
            CLIENT_PORTAL_DASHBOARD_HTML,
            client_id=client_id,
            client_name=client_name,
        )
    else:
        return render_template_string(
            CLIENT_PORTAL_LOGIN_HTML,
            client_id=client_id,
            client_name=client_name,
        )


@app.route("/portal/<client_id>/login", methods=["POST"])
def client_portal_login(client_id):
    if client_id not in CLIENT_CREDENTIALS:
        return jsonify({"error": "Invalid portal"}), 404

    data = request.get_json(silent=True) or {}
    password = data.get("password", "")

    if password == CLIENT_CREDENTIALS[client_id]["password"]:
        session[f"portal_{client_id}"] = True
        return jsonify({"success": True})
    return jsonify({"error": "Invalid password"}), 401


@app.route("/portal/<client_id>/api/leads", methods=["GET"])
def client_portal_api_leads(client_id):
    if client_id not in CLIENT_CREDENTIALS:
        return jsonify({"error": "Invalid portal"}), 404
    if not session.get(f"portal_{client_id}"):
        return jsonify({"error": "Not authenticated"}), 401

    leads = get_client_portal_leads(client_id)
    total = len(leads)
    now = datetime.now()
    this_month = sum(
        1 for l in leads
        if l.get("created_at") and l["created_at"][:7] == now.strftime("%Y-%m")
    )
    return jsonify({"leads": leads, "total": total, "this_month": this_month})


@app.route("/portal/<client_id>/logout", methods=["POST"])
def client_portal_logout(client_id):
    session.pop(f"portal_{client_id}", None)
    return jsonify({"success": True})


# ── Dashboard Page ───────────────────────────────────────────────────────────


@app.route("/dashboard")
@login_required
def dashboard_page():
    return render_template_string(DASHBOARD_HTML)


# ── Invoice Endpoints ───────────────────────────────────────────────────────

@app.route("/api/invoices", methods=["GET"])
@login_required
def api_list_invoices():
    conn = get_db()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM invoices ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/invoices", methods=["POST"])
@login_required
def api_create_invoice():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    client_name = (data.get("client_name") or "").strip()
    client_email = (data.get("client_email") or "").strip()
    amount = data.get("amount")
    description = (data.get("description") or "").strip()
    due_date = (data.get("due_date") or "").strip()
    lead_id = (data.get("lead_id") or "").strip()

    if not client_name or not client_email:
        return jsonify({"error": "Client name and email are required"}), 400

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid amount"}), 400

    has_sub = data.get("has_subscription", False)
    sub_amount = 0
    sub_interval = "month"
    sub_description = ""
    if has_sub:
        try:
            sub_amount = float(data.get("sub_amount", 0))
            if sub_amount <= 0:
                has_sub = False
            sub_interval = data.get("sub_interval", "month") or "month"
            sub_description = (data.get("sub_description") or "").strip()
        except (TypeError, ValueError):
            has_sub = False

    invoice_id = str(uuid.uuid4())[:8]
    invoice_number = generate_invoice_number()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO invoices
           (id, lead_id, client_name, client_email, amount, description, due_date, invoice_number,
            has_subscription, sub_amount, sub_interval, sub_description)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (invoice_id, lead_id, client_name, client_email, amount, description, due_date, invoice_number,
         1 if has_sub else 0, sub_amount, sub_interval, sub_description),
    )
    conn.commit()
    conn.close()

    inv = get_invoice_by_id(invoice_id)
    return jsonify(inv), 201


@app.route("/api/invoices/<invoice_id>", methods=["GET"])
@login_required
def api_get_invoice(invoice_id):
    inv = get_invoice_by_id(invoice_id)
    if not inv:
        return jsonify({"error": "Invoice not found"}), 404
    return jsonify(inv)


@app.route("/api/invoices/<invoice_id>", methods=["PUT"])
@login_required
def api_update_invoice(invoice_id):
    inv = get_invoice_by_id(invoice_id)
    if not inv:
        return jsonify({"error": "Invoice not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    allowed_updates = {"status", "paid_at", "description", "due_date", "amount",
                       "has_subscription", "sub_amount", "sub_interval", "sub_description",
                       "stripe_subscription_id", "sub_status"}
    updates = {k: v for k, v in data.items() if k in allowed_updates}

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    # If marking as paid, set paid_at
    if updates.get("status") == "paid" and not inv.get("paid_at"):
        updates["paid_at"] = datetime.now().isoformat()

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [invoice_id]

    conn = get_db()
    cur = conn.cursor()
    cur.execute(f"UPDATE invoices SET {set_clause} WHERE id = %s", values)
    conn.commit()
    conn.close()

    updated = get_invoice_by_id(invoice_id)
    return jsonify(updated)


@app.route("/api/invoices/<invoice_id>/send", methods=["POST"])
@login_required
def api_send_invoice(invoice_id):
    inv = get_invoice_by_id(invoice_id)
    if not inv:
        return jsonify({"error": "Invoice not found"}), 404

    # Create Stripe payment link for the one-time setup fee
    stripe_url = None
    try:
        amount_cents = int(inv["amount"] * 100)
        s = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"Setup - Invoice #{inv['invoice_number']} — {inv['description'] or 'Automation Services'}"},
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"https://automate-pro-production.up.railway.app/checkout/success?invoice={inv['id']}",
            cancel_url=f"https://automate-pro-production.up.railway.app/invoice/{inv['id']}",
            metadata={"invoice_id": inv["id"], "payment_type": "setup"}
        )
        stripe_url = s.url
    except Exception as e:
        stripe_url = None

    # Create Stripe subscription link if invoice has subscription
    sub_stripe_url = None
    sub_session_id = None
    if inv.get("has_subscription") and inv.get("sub_amount", 0) > 0:
        try:
            sub_amt_cents = int(inv["sub_amount"] * 100)
            sub_desc = inv.get("sub_description", "") or "Monthly Retainer"
            sub_int = inv.get("sub_interval", "month")
            # Create a product + price for the subscription
            sub_product = stripe.Product.create(name=f"Subscription - Invoice #{inv['invoice_number']} — {sub_desc}")
            sub_price = stripe.Price.create(
                product=sub_product.id,
                unit_amount=sub_amt_cents,
                currency="usd",
                recurring={"interval": sub_int},
            )
            sub_session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price": sub_price.id, "quantity": 1}],
                mode="subscription",
                success_url=f"https://automate-pro-production.up.railway.app/checkout/success?invoice={inv['id']}",
                cancel_url=f"https://automate-pro-production.up.railway.app/invoice/{inv['id']}",
                metadata={"invoice_id": inv["id"], "payment_type": "subscription"}
            )
            sub_stripe_url = sub_session.url
            sub_session_id = sub_session.id
        except Exception as e:
            sub_stripe_url = None

    subject = f"Invoice #{inv['invoice_number']} from Automate Pro"
    body = build_invoice_email_body(inv, stripe_url, sub_stripe_url)
    
    conn = get_db()
    cur = conn.cursor()
    if (stripe_url or sub_stripe_url) and inv["status"] == "draft":
        cur.execute("UPDATE invoices SET status = %s WHERE id = %s", ("sent", invoice_id))

    result = send_email(inv["client_email"], subject, body)
    conn.commit()
    conn.close()
    
    if result.get("success"):
        return jsonify({
            "success": True,
            "message": f"Invoice sent to {inv['client_email']}",
            "stripe_url": stripe_url,
            "sub_stripe_url": sub_stripe_url
        })
    return jsonify({"error": "Failed to send email"}), 500


# ── Stripe Endpoints ────────────────────────────────────────────────────────

@app.route("/api/invoices/<invoice_id>/cancel-subscription", methods=["POST"])
@login_required
def api_cancel_subscription(invoice_id):
    """Cancel a subscription — emails Emilio so he can take the website down."""
    inv = get_invoice_by_id(invoice_id)
    if not inv:
        return jsonify({"error": "Invoice not found"}), 404
    if not inv.get("has_subscription"):
        return jsonify({"error": "This invoice has no subscription"}), 400
    if inv.get("sub_status") in ("cancelled", "none"):
        return jsonify({"error": f"Subscription is already {inv.get('sub_status', 'none')}"}), 400

    # Cancel in Stripe if we have a subscription ID
    stripe_sub_id = inv.get("stripe_subscription_id", "")
    if stripe_sub_id:
        try:
            stripe.Subscription.modify(stripe_sub_id, cancel_at_period_end=True)
        except Exception as e:
            print(f"[Cancel Sub] Stripe error cancelling {stripe_sub_id}: {e}")
            # Still proceed with notification even if Stripe fails

    # Mark subscription as cancelled in DB
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE invoices SET sub_status = %s WHERE id = %s",
        ("cancelled", invoice_id),
    )
    conn.commit()
    conn.close()

    # Email Emilio to take the website down
    SEPARATOR = "=" * 50
    notify_subject = f"🚫 Subscription Cancelled — {inv['client_name']} ({inv['client_email']})"
    notify_body = (
        "Subscription Cancelled!\n"
        f"{SEPARATOR}\n"
        f"Client: {inv['client_name']}\n"
        f"Email: {inv['client_email']}\n"
        f"Invoice: #{inv['invoice_number']}\n"
        f"Setup fee paid: ${inv['amount']:.2f}\n"
        f"Subscription: ${inv.get('sub_amount', 0):.2f}/{inv.get('sub_interval', 'month')}\n"
        f"Subscription desc: {inv.get('sub_description', '') or 'N/A'}\n"
        f"Stripe sub ID: {stripe_sub_id or 'N/A'}\n"
        f"Cancelled at: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        "\n"
        "➡ ACTION REQUIRED: Take down the client's website/automation!\n"
        f"{SEPARATOR}\n"
    )
    send_email(NOTIFY_EMAIL, notify_subject, notify_body)

    return jsonify({
        "success": True,
        "message": f"Subscription cancelled for {inv['client_name']}. You've been notified."
    })


@app.route("/invoice/<invoice_id>")
@app.route("/inv/<inv_num>")
def public_invoice_view(invoice_id):
    """Public invoice view — no auth required, beautiful printable page."""
    inv = get_invoice_by_id(invoice_id)
    if not inv:
        return render_template_string(INVOICE_NOT_FOUND_HTML)
    return render_template_string(INVOICE_PAGE_HTML, inv=inv)


@app.route("/api/config", methods=["GET"])
def api_config():
    return jsonify({"publishableKey": STRIPE_PUBLISHABLE_KEY})


@app.route("/api/create-checkout-session", methods=["POST"])
def api_create_checkout_session():
    data = request.get_json() or {}
    
    # Handle invoice payment (from Pay Now button on invoice page)
    if data.get("type") == "invoice" and data.get("amount"):
        try:
            amount_cents = int(data["amount"])
            desc = data.get("description", "Invoice Payment")
            inv_id = data.get("invoice_id", "")
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": desc},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }],
                mode="payment",
                success_url=request.host_url + "checkout/success?invoice=" + inv_id,
                cancel_url=request.host_url + "invoice/" + inv_id,
                metadata={"invoice_id": inv_id}
            )
            return jsonify({"url": session.url, "sessionId": session.id})
        except Exception as e:
            return jsonify({"error": str(e)}), 400
    
    # Support custom invoice payment
    if data.get("plan") == "custom" and data.get("amount"):
        try:
            amount_cents = int(float(data["amount"]) * 100)
            desc = data.get("description", "Invoice Payment")
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": desc},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }],
                mode="payment",
                success_url=request.host_url + "checkout/success",
                cancel_url=request.host_url,
            )
            return jsonify({"url": session.url, "sessionId": session.id})
        except Exception as e:
            return jsonify({"error": str(e)}), 400
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    plan_key = data.get("plan")
    if plan_key not in PRICE_CACHE:
        return jsonify({"error": f"Unknown plan: {plan_key}"}), 400

    # Resolve product name for display
    plan = PLANS[plan_key]
    success_url = data.get(
        "success_url", request.host_url.rstrip("/") + "/checkout/success"
    )
    cancel_url = data.get("cancel_url", request.host_url.rstrip("/") + "/")

    try:
        session_data = {
            "line_items": [
                {
                    "price": PRICE_CACHE[plan_key],
                    "quantity": 1,
                }
            ],
            "mode": "payment" if plan["type"] == "one_time" else "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
        }

        # Attach lead_id as metadata if provided
        lead_id = data.get("lead_id")
        if lead_id:
            session_data["metadata"] = {"lead_id": lead_id, "plan_key": plan_key}
        else:
            session_data["metadata"] = {"plan_key": plan_key}

        checkout_session = stripe.checkout.Session.create(**session_data)

        return jsonify({"sessionId": checkout_session.id, "url": checkout_session.url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendly-webhook", methods=["POST"])
def api_calendly_webhook():
    """Receive Calendly webhook when someone books a call."""
    try:
        data = request.get_json(silent=True) or {}
        payload = data.get("payload", {})
        event_type = data.get("event", "")
        
        # Only process when a new event is created (call booked)
        if "invitee.created" in event_type or "invitee.canceled" in event_type:
            invitee = payload.get("invitee", {}) or {}
            email = invitee.get("email", "")
            name = invitee.get("name", "")
            start_time = payload.get("event", {}).get("start_time", "") if payload.get("event") else ""
            
            if email:
                conn = get_db()
                cur = conn.cursor()
                # Find the lead by email
                lead = cur.execute(
                    "SELECT id, status FROM leads WHERE email = %s ORDER BY created_at DESC LIMIT 1",
                    (email,)
                ).fetchone()
                
                if lead:
                    if "invitee.created" in event_type:
                        new_status = "call_scheduled"
                        # Format the follow-up date from the start_time
                        follow_up = start_time[:10] if start_time else ""
                        cur.execute(
                            "UPDATE leads SET status = %s, follow_up_date = %s WHERE id = %s",
                            (new_status, follow_up, lead[0])
                        )
                        conn.commit()
                    elif "invitee.canceled" in event_type:
                        cur.execute(
                            "UPDATE leads SET status = 'new' WHERE id = %s AND status = 'call_scheduled'",
                            (lead[0],)
                        )
                        conn.commit()
                
                conn.close()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 200  # Return 200 so Calendly doesn't retry


@app.route("/api/stripe-webhook", methods=["POST"])
def api_stripe_webhook():
    """Handle Stripe webhook events (no signature verification in test mode)."""
    payload = request.get_data(as_text=True)

    try:
        event = json.loads(payload)
    except Exception:
        return jsonify({"error": "Invalid payload"}), 400

    event_type = event.get("type")
    print(f"  [Webhook] Received event: {event_type}")

    if event_type == "checkout.session.completed":
        session_data = event.get("data", {}).get("object", {})
        handle_checkout_completed(session_data)
    elif event_type == "checkout.session.expired":
        print(f"  [Webhook] Checkout session expired: {event.get('id')}")
    elif event_type == "customer.subscription.deleted":
        sub_data = event.get("data", {}).get("object", {})
        handle_subscription_deleted(sub_data)
    elif event_type == "invoice.payment_succeeded":
        inv_data = event.get("data", {}).get("object", {})
        # Check if this is a subscription invoice (recurring payment)
        if inv_data.get("subscription"):
            handle_subscription_payment(inv_data)
    else:
        print(f"  [Webhook] Unhandled event type: {event_type}")

    return jsonify({"received": True}), 200


def handle_checkout_completed(session_data):
    """Process a successful checkout completion."""
    session_id = session_data.get("id")
    metadata = session_data.get("metadata", {}) or {}
    plan_key = metadata.get("plan_key", "unknown")
    lead_id = metadata.get("lead_id")
    invoice_id = metadata.get("invoice_id", "")
    customer_email = session_data.get("customer_details", {}).get("email", "") or ""

    # Calculate amount from the session
    amount_total = session_data.get("amount_total", 0) / 100.0
    currency = session_data.get("currency", "usd") or "usd"

    # Handle invoice payments (both setup fees and subscription signups)
    if invoice_id:
        payment_type = metadata.get("payment_type", "setup")
        conn = get_db()
        cur = conn.cursor()
        
        if payment_type == "setup":
            cur.execute("UPDATE invoices SET status = 'paid', paid_at = NOW() WHERE id = %s", (invoice_id,))
        elif payment_type == "subscription":
            # Save the subscription ID from the checkout session
            sub_id = session_data.get("subscription", "")
            if sub_id and isinstance(sub_id, str):
                cur.execute(
                    "UPDATE invoices SET stripe_subscription_id = %s, sub_status = 'active' WHERE id = %s",
                    (sub_id, invoice_id),
                )
            elif isinstance(sub_id, dict):
                sid = sub_id.get("id", "")
                if sid:
                    cur.execute(
                        "UPDATE invoices SET stripe_subscription_id = %s, sub_status = 'active' WHERE id = %s",
                        (sid, invoice_id),
                    )
        
        conn.commit()
        inv = cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,)).fetchone()
        conn.close()
        if inv:
            if payment_type == "setup":
                receipt_subject = f"Receipt — Invoice #{inv[8]} Paid"
                receipt_body = f"Hi {inv[2]},\n\nYour invoice #{inv[8]} for ${inv[4]:.2f} has been paid.\n\nThank you for your business!\n\nEmilio\nAutomate Pro"
                send_email(inv[3], receipt_subject, receipt_body)
                notify = f"✅ Invoice #{inv[8]} setup fee paid — ${inv[4]:.2f} from {inv[2]}"
                send_email("emilio.pegolo1@gmail.com", "Payment Received (Setup): Invoice #" + inv[8], notify)
            elif payment_type == "subscription":
                sub_amt = inv[12] if len(inv) > 12 else 0  # sub_amount column
                sub_int = inv[13] if len(inv) > 13 else "month"  # sub_interval
                notify = f"✅ Subscription started — ${sub_amt:.2f}/{sub_int} from {inv[2]} ({inv[3]})"
                send_email("emilio.pegolo1@gmail.com", "🔄 Subscription Started: " + inv[10], notify)
        return

    plan = PLANS.get(plan_key, {})
    plan_name = plan.get("name", plan_key)
    payment_type = "setup" if plan.get("type") == "one_time" else "subscription"

    payment_id = str(uuid.uuid4())[:8]
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO payments
           (id, lead_id, stripe_session_id, amount, currency, plan_name, payment_type, status, customer_email)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            payment_id,
            lead_id,
            session_id,
            amount_total,
            currency,
            plan_name,
            payment_type,
            "completed",
            customer_email,
        ),
    )

    # If there's a lead_id, update the lead status to paid and set revenue
    if lead_id:
        cur.execute(
            "UPDATE leads SET status = 'paid', revenue = COALESCE(revenue, 0) + %s WHERE id = %s",
            (amount_total, lead_id),
        )

    conn.commit()
    conn.close()

    print(f"  [Payment] Recorded ${amount_total:.2f} from {plan_name} ({customer_email})")

    # Send notification to Emilio
    try:
        subject = f"\U0001f4b0 New Payment: ${amount_total:.2f} - {plan_name}"
        body = (
            f"New payment received!\n"
            f"\n"
            f"Plan: {plan_name}\n"
            f"Amount: ${amount_total:.2f}\n"
            f"Customer: {customer_email}\n"
            f"Payment ID: {payment_id}\n"
            f"Session ID: {session_id}\n"
            f"Type: {payment_type}\n"
            f"Lead ID: {lead_id or 'N/A'}\n"
        )
        send_email(NOTIFY_EMAIL, subject, body)
        print(f"  [Payment] Notification sent to {NOTIFY_EMAIL}")
    except Exception as e:
        print(f"  [Payment] Failed to send notification: {e}")


def handle_subscription_deleted(sub_data):
    """Handle subscription deleted/cancelled from Stripe webhook."""
    sub_id = sub_data.get("id", "")
    if not sub_id:
        return
    # Find the invoice with this subscription ID
    conn = get_db()
    cur = conn.cursor()
    try:
        # Check if column exists first
        inv = cur.execute(
            "SELECT * FROM invoices WHERE stripe_subscription_id = %s AND has_subscription = 1",
            (sub_id,),
        ).fetchone()
        if inv:
            inv_dict = dict(inv)
            cur.execute(
                "UPDATE invoices SET sub_status = 'cancelled' WHERE stripe_subscription_id = %s",
                (sub_id,),
            )
            conn.commit()
            # Notify Emilio to take the website down
            notify = (
                f"🚫 Subscription Cancelled via Stripe!\n"
                f"\n"
                f"Client: {inv_dict.get('client_name', 'Unknown')}\n"
                f"Email: {inv_dict.get('client_email', 'Unknown')}\n"
                f"Invoice: #{inv_dict.get('invoice_number', 'N/A')}\n"
                f"Stripe Sub ID: {sub_id}\n"
                f"\n"
                f"➡ ACTION REQUIRED: Take down their website/automation!\n"
            )
            send_email(NOTIFY_EMAIL, "🚫 Subscription Cancelled (Stripe Webhook)", notify)
    except Exception as e:
        print(f"[Webhook] Error handling subscription deletion: {e}")
    finally:
        conn.close()


def handle_subscription_payment(inv_data):
    """Handle recurring subscription payment success."""
    sub_id = inv_data.get("subscription", "")
    amount_paid = inv_data.get("amount_paid", 0) / 100.0
    customer_email = inv_data.get("customer_email", "") or inv_data.get("customer_details", {}).get("email", "")
    if not sub_id:
        return
    conn = get_db()
    cur = conn.cursor()
    try:
        inv = cur.execute(
            "SELECT * FROM invoices WHERE stripe_subscription_id = %s AND has_subscription = 1",
            (sub_id,),
        ).fetchone()
        if inv:
            notify = f"🔄 Recurring payment received: ${amount_paid:.2f} from {inv[2]} ({inv[3]})"
            send_email(NOTIFY_EMAIL, f"💰 Subscription Payment — ${amount_paid:.2f}", notify)
    except Exception as e:
        print(f"[Webhook] Error handling subscription payment: {e}")
    finally:
        conn.close()


@app.route("/checkout/success")
def checkout_success():
    invoice_id = request.args.get("invoice", "")
    paid_name = ""
    paid_amount = ""
    if invoice_id:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE invoices SET status = 'paid', paid_at = NOW() WHERE id = %s AND status != 'paid'", (invoice_id,))
            conn.commit()
            inv = cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,)).fetchone()
            conn.close()
            if inv:
                paid_name = inv[2]
                paid_amount = inv[4]
                receipt_num = inv[8]
                # Send receipt
                send_email(inv[3], "Receipt - Invoice #" + str(receipt_num) + " Paid", "Hi " + str(inv[2]) + ",\n\nYour invoice #" + str(receipt_num) + " for $" + str(inv[4]) + " has been paid.\n\nThank you for your business!\n\nEmilio\nAutomate Pro")
                # Notify Emilio
                send_email("emilio.pegolo1@gmail.com", "Payment Received: Invoice #" + str(receipt_num), "Client: " + str(inv[2]) + "\nAmount: $" + str(inv[4]) + "\nInvoice: #" + str(receipt_num))
        except Exception as e:
            pass
    return render_template_string(CHECKOUT_SUCCESS_HTML)


# ── HTML Templates ───────────────────────────────────────────────────────────


CHECKOUT_SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Payment Successful — Automate Pro</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0a0f;
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .success-card {
    background: #12121a;
    border: 1px solid #2a2a3a;
    border-radius: 16px;
    padding: 60px 48px;
    width: 100%;
    max-width: 500px;
    text-align: center;
  }
  .checkmark {
    width: 72px;
    height: 72px;
    margin: 0 auto 24px;
    background: rgba(34,197,94,0.1);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 36px;
    color: #00d4aa;
  }
  .success-card h1 {
    font-size: 28px;
    font-weight: 700;
    color: #fff;
    margin-bottom: 12px;
  }
  .success-card p {
    color: #888;
    font-size: 15px;
    line-height: 1.6;
    margin-bottom: 32px;
  }
  .success-card .btn {
    display: inline-block;
    padding: 14px 32px;
    border-radius: 8px;
    background: #ff8c42;
    color: #0a0a0f;
    font-weight: 600;
    font-size: 15px;
    text-decoration: none;
    transition: background 0.2s;
  }
  .success-card .btn:hover {
    background: #ff9f5e;
  }
  .success-card .brand {
    font-size: 14px;
    font-weight: 800;
    color: #ff8c42;
    margin-bottom: 24px;
  }
</style>
</head>
<body>
<div class="success-card">
  <div class="brand">Automate Pro</div>
  <div class="checkmark">✓</div>
  <h1>Payment Successful!</h1>
  <p>We'll be in touch within 24 hours to get you set up.<br>Check your email for a confirmation message.</p>
  <a href="/" class="btn">Back to Home</a>
</div>
</body>
</html>"""


INVOICE_NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invoice Not Found — Automate Pro</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0a0f;
    color: #e0e0e0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .notfound-card {
    background: #12121a;
    border: 1px solid #2a2a3a;
    border-radius: 16px;
    padding: 60px 48px;
    width: 100%;
    max-width: 500px;
    text-align: center;
  }
  .notfound-card .icon {
    font-size: 48px;
    margin-bottom: 16px;
  }
  .notfound-card h1 {
    font-size: 28px;
    font-weight: 700;
    color: #fff;
    margin-bottom: 12px;
  }
  .notfound-card p {
    color: #888;
    font-size: 15px;
    line-height: 1.6;
  }
  .notfound-card .brand {
    font-size: 14px;
    font-weight: 800;
    color: #ff8c42;
    margin-bottom: 24px;
  }
</style>
</head>
<body>
<div class="notfound-card">
  <div class="brand">Automate Pro</div>
  <div class="icon">🔍</div>
  <h1>Invoice Not Found</h1>
  <p>This invoice doesn't exist or has been removed.<br>If you believe this is an error, please contact us.</p>
</div>
</body>
</html>"""


INVOICE_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Invoice {{inv['invoice_number']}} — Automate Pro</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #0a0a0f;
    --surface: #12121a;
    --surface-2: #1a1a26;
    --border: #2a2a3a;
    --text: #e0e0e0;
    --text-muted: #888;
    --accent: #ff8c42;
    --green: #00d4aa;
    --red: #ff4d6a;
    --blue: #4dabf7;
  }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 40px 24px;
  }
  .invoice-container {
    max-width: 800px;
    margin: 0 auto;
  }
  .invoice-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 48px;
    position: relative;
  }
  .invoice-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 40px;
    padding-bottom: 24px;
    border-bottom: 1px solid var(--border);
  }
  .invoice-header .company {
    font-size: 28px;
    font-weight: 800;
    color: var(--accent);
  }
  .invoice-header .company-sub {
    font-size: 13px;
    color: var(--text-muted);
    margin-top: 4px;
  }
  .invoice-header .invoice-number {
    text-align: right;
  }
  .invoice-header .invoice-number .num {
    font-size: 22px;
    font-weight: 700;
    color: #fff;
  }
  .invoice-header .invoice-number .label {
    font-size: 12px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .invoice-parties {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 32px;
    margin-bottom: 40px;
  }
  .invoice-parties .party h3 {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin-bottom: 8px;
    font-weight: 600;
  }
  .invoice-parties .party .name {
    font-size: 16px;
    font-weight: 600;
    color: #fff;
    margin-bottom: 4px;
  }
  .invoice-parties .party .email {
    font-size: 14px;
    color: var(--text-muted);
  }

  .invoice-dates {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 40px;
    padding: 20px;
    background: var(--surface-2);
    border-radius: 10px;
  }
  .invoice-dates .date-item .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin-bottom: 4px;
  }
  .invoice-dates .date-item .value {
    font-size: 15px;
    color: #fff;
    font-weight: 500;
  }

  .invoice-items {
    margin-bottom: 40px;
  }
  .invoice-items table {
    width: 100%;
    border-collapse: collapse;
  }
  .invoice-items th {
    text-align: left;
    padding: 12px 16px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    font-weight: 600;
  }
  .invoice-items td {
    padding: 16px;
    font-size: 14px;
    border-bottom: 1px solid var(--border);
  }
  .invoice-items td:last-child {
    text-align: right;
    font-weight: 600;
    color: #fff;
  }
  .invoice-items td.desc {
    color: var(--text-muted);
    font-size: 13px;
  }

  .invoice-total {
    text-align: right;
    padding-top: 16px;
    border-top: 2px solid var(--accent);
    margin-bottom: 32px;
  }
  .invoice-total .total-label {
    font-size: 14px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 4px;
  }
  .invoice-total .total-amount {
    font-size: 36px;
    font-weight: 800;
    color: #fff;
  }

  .status-badge {
    display: inline-block;
    padding: 6px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    text-transform: capitalize;
  }
  .status-badge.draft { background: rgba(136,136,136,0.15); color: var(--text-muted); }
  .status-badge.sent { background: rgba(77,171,247,0.15); color: var(--blue); }
  .status-badge.paid { background: rgba(34,197,94,0.15); color: var(--green); }
  .status-badge.overdue { background: rgba(255,77,106,0.15); color: var(--red); }
  .status-badge.cancelled { background: rgba(136,136,136,0.15); color: var(--text-muted); }

  .invoice-actions {
    display: flex;
    gap: 12px;
    justify-content: center;
    margin-top: 24px;
  }
  .btn {
    padding: 12px 24px;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    font-family: inherit;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }
  .btn-accent {
    background: var(--accent);
    color: #fff;
  }
  .btn-accent:hover {
    background: #e07a30;
  }
  .btn-outline {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
  }
  .btn-outline:hover {
    border-color: var(--text-muted);
  }

  .invoice-footer {
    text-align: center;
    padding-top: 24px;
    border-top: 1px solid var(--border);
    margin-top: 24px;
  }
  .invoice-footer p {
    font-size: 13px;
    color: var(--text-muted);
  }
  .invoice-footer .thankyou {
    font-size: 16px;
    color: var(--accent);
    font-weight: 600;
    margin-bottom: 8px;
  }

  @media print {
    body {
      background: #fff;
      color: #000;
      padding: 0;
    }
    .invoice-card {
      background: #fff;
      border: none;
      border-radius: 0;
      padding: 40px;
      box-shadow: none;
    }
    .invoice-header .company { color: #ff8c42; }
    .invoice-header .invoice-number .num { color: #000; }
    .invoice-parties .party .name { color: #000; }
    .invoice-dates { background: #f5f5f5; }
    .invoice-dates .date-item .value { color: #000; }
    .invoice-items td:last-child { color: #000; }
    .invoice-total .total-amount { color: #000; }
    .invoice-total { border-top-color: #ff8c42; }
    .no-print { display: none !important; }
    .status-badge { border: 1px solid #ddd; }
    .status-badge.paid { border-color: var(--green); color: #000; }
  }

  @media (max-width: 600px) {
    .invoice-card { padding: 24px; }
    .invoice-header { flex-direction: column; gap: 16px; }
    .invoice-header .invoice-number { text-align: left; }
    .invoice-parties { grid-template-columns: 1fr; gap: 20px; }
    .invoice-dates { grid-template-columns: 1fr; }
    .invoice-actions { flex-direction: column; align-items: stretch; }
    .invoice-total .total-amount { font-size: 28px; }
  }
</style>
</head>
<body>
<div class="invoice-container">
  <div class="invoice-card">
    <!-- Header -->
    <div class="invoice-header">
      <div>
        <div class="company">Automate Pro</div>
        <div class="company-sub">AI Business Automation</div>
      </div>
      <div class="invoice-number">
        <div class="label">Invoice</div>
        <div class="num">{{inv['invoice_number']}}</div>
      </div>
    </div>

    <!-- Parties -->
    <div class="invoice-parties">
      <div class="party">
        <h3>Bill To</h3>
        <div class="name">{{inv['client_name']}}</div>
        <div class="email">{{inv['client_email']}}</div>
      </div>
      <div class="party">
        <h3>From</h3>
        <div class="name">Automate Pro</div>
        <div class="email">emilio.pegolo1@gmail.com</div>
      </div>
    </div>

    <!-- Dates -->
    <div class="invoice-dates">
      <div class="date-item">
        <div class="label">Invoice Date</div>
        <div class="value">{{inv['created_at'][:10] if inv['created_at'] else '—'}}</div>
      </div>
      <div class="date-item">
        <div class="label">Due Date</div>
        <div class="value">{{inv.get('due_date') or 'Upon Receipt'}}</div>
      </div>
    </div>

    <!-- Items -->
    <div class="invoice-items">
      <table>
        <thead>
          <tr>
            <th>Description</th>
            <th style="text-align:right;width:120px;">Amount</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <div style="font-weight:600;">Setup Fee — {{inv['description'] or 'Professional Automation Service'}}</div>
              <div class="desc">{{inv['invoice_number']}}</div>
            </td>
            <td>${{'{:,.2f}'.format(inv['amount'])}}</td>
          </tr>
          {% if inv.get('has_subscription') and inv.get('sub_amount', 0) > 0 %}
          <tr>
            <td>
              <div style="font-weight:600;">Subscription — {{inv.get('sub_description') or 'Monthly Retainer'}}</div>
              <div class="desc">Recurring {{inv.get('sub_interval') or 'month'}}ly</div>
            </td>
            <td>${{'{:,.2f}'.format(inv['sub_amount'])}}/<span style="font-size:12px;color:var(--text-muted);">{{inv.get('sub_interval') or 'mo'}}</span></td>
          </tr>
          {% endif %}
        </tbody>
      </table>
    </div>

    <!-- Total -->
    <div class="invoice-total">
      <div class="total-label">Setup Fee</div>
      <div class="total-amount">${{'{:,.2f}'.format(inv['amount'])}}</div>
      {% if inv.get('has_subscription') and inv.get('sub_amount', 0) > 0 %}
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border);">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <div>
            <div style="font-size:14px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Subscription</div>
            <div style="font-size:13px;color:var(--blue);">{{inv.get('sub_description') or 'Monthly Retainer'}} — ${{'{:,.2f}'.format(inv['sub_amount'])}}/{{inv.get('sub_interval') or 'month'}}</div>
          </div>
          <div style="font-size:20px;font-weight:700;color:#fff;">${{'{:,.2f}'.format(inv['sub_amount'])}}<span style="font-size:13px;font-weight:400;color:var(--text-muted);">/{{inv.get('sub_interval') or 'mo'}}</span></div>
        </div>
        <div style="margin-top:8px;">
          <span class="status-badge" style="{% if inv.get('sub_status') == 'active' %}background:rgba(34,197,94,0.15);color:var(--green);{% elif inv.get('sub_status') == 'cancelled' %}background:rgba(255,77,106,0.15);color:var(--red);{% else %}background:rgba(77,171,247,0.15);color:var(--blue);{% endif %}">{{inv.get('sub_status') or 'pending'}}</span>
        </div>
      </div>
      {% endif %}
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border);">
        <div style="font-size:14px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Status</div>
        <span class="status-badge {{inv['status']}}">{{inv['status']}}</span>
      </div>
    </div>

    <!-- Actions -->
    <div class="invoice-actions no-print">
      <button class="btn btn-accent" onclick="window.print()">🖨️ Print / Save PDF</button>
    </div>

    <!-- Footer -->
    <div class="invoice-footer">
      <div class="thankyou">Thank you for your business!</div>
      <p>Automate Pro — AI-powered business automation solutions</p>
      <p style="margin-top:4px;">emilio.pegolo1@gmail.com</p>
    </div>
  </div>
</div>
<script>
function payInvoiceStripe() {
  var el = document.getElementById("pay-result");
  el.textContent = "Processing...";
  var amt = {{ inv.amount }};
  fetch("/api/create-checkout-session", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({type:"invoice", amount: Math.round(amt*100), description: "Invoice {{ inv.invoice_number }}", invoice_id: "{{ inv.id }}"})
  }).then(function(r){return r.json();}).then(function(d){
    if (d.url) window.location.href = d.url;
    else el.textContent = "Error: " + (d.error || "Unknown");
  }).catch(function(e){el.textContent = "Error: " + e.message;});
}
</script></body>
</html>"""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Automate Pro — Login</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Fira Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0F172A;
    color: #F8FAFC;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .login-card {
    background: #1E293B;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 48px 40px;
    width: 100%;
    max-width: 400px;
    text-align: center;
  }
  .login-card h1 { font-size: 24px; margin-bottom: 4px; color: #fff; }
  .login-card .subtitle { color: #94A3B8; font-size: 14px; margin-bottom: 32px; }
  .login-card .brand { color: #F59E0B; font-weight: 700; font-size: 28px; margin-bottom: 8px; }
  .login-card .lock-icon { margin-bottom: 12px; color: #F59E0B; }
  .login-card input {
    width: 100%;
    padding: 14px 16px;
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.08);
    background: #0F172A;
    color: #fff;
    font-size: 16px;
    outline: none;
    transition: border-color 0.2s;
    margin-bottom: 16px;
    font-family: inherit;
  }
  .login-card input:focus { border-color: #F59E0B; }
  .login-card input::placeholder { color: #64748B; }
  .login-card button {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 10px;
    background: #F59E0B;
    color: #0F172A;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
    font-family: inherit;
  }
  .login-card button:hover { background: #D97706; }
  .login-card .error { color: #EF4444; font-size: 14px; margin-top: 12px; display: none; }
</style>
</head>
<body>
<div class="login-card">
  <div class="lock-icon">
    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
    </svg>
  </div>
  <h1>Admin Dashboard</h1>
  <p class="subtitle">Enter your password to continue</p>
  <input type="password" id="password" placeholder="Password" autocomplete="current-password">
  <button onclick="login()">Sign In</button>
  <div class="error" id="error"></div>
</div>
<script>
document.getElementById('password').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') login();
});
async function login() {
  const pwd = document.getElementById('password').value;
  const err = document.getElementById('error');
  if (!pwd) { err.textContent = 'Please enter a password'; err.style.display = 'block'; return; }
  try {
    const res = await fetch('/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({password:pwd}) });
    const data = await res.json();
    if (data.success) { window.location.href = '/dashboard'; }
    else { err.textContent = 'Invalid password'; err.style.display = 'block'; }
  } catch(e) { err.textContent = 'Connection error'; err.style.display = 'block'; }
}
</script>
</body>
</html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Automate Pro — Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #0F172A;
    --surface: #1E293B;
    --surface-2: #334155;
    --border: rgba(255,255,255,0.08);
    --text: #F8FAFC;
    --text-muted: #94A3B8;
    --accent: #F59E0B;
    --accent-hover: #D97706;
    --green: #22C55E;
    --green-dim: #16A34A;
    --red: #EF4444;
    --pink: #EC4899;
    --blue: #3B82F6;
    --yellow: #EAB308;
  }
  html, body { height: 100%; }
  body {
    font-family: 'Fira Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    display: flex;
  }
  .mono { font-family: 'Fira Code', 'Courier New', monospace; }

  /* ── Sidebar ── */
  .sidebar {
    width: 240px;
    min-width: 240px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: 0;
    height: 100vh;
    position: sticky;
    top: 0;
  }
  .sidebar .brand {
    padding: 24px 20px;
    font-size: 22px;
    font-weight: 800;
    color: var(--accent);
    border-bottom: 1px solid var(--border);
  }
  .sidebar nav { flex: 1; padding: 16px 0; }
  .sidebar nav a {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 20px;
    color: var(--text-muted);
    text-decoration: none;
    font-size: 14px;
    font-weight: 500;
    transition: all 0.15s;
    border-left: 3px solid transparent;
  }
  .sidebar nav a svg { flex-shrink: 0; }
  .sidebar nav a:hover, .sidebar nav a.active {
    color: var(--text);
    background: rgba(255,255,255,0.04);
  }
  .sidebar nav a.active { border-left-color: var(--accent); color: var(--accent); }
  .sidebar .logout-section {
    padding: 16px 20px;
    border-top: 1px solid var(--border);
  }
  .sidebar .logout-btn {
    width: 100%;
    padding: 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: transparent;
    color: var(--text-muted);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    font-family: inherit;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  .sidebar .logout-btn svg { flex-shrink: 0; }
  .sidebar .logout-btn:hover {
    color: var(--red);
    border-color: var(--red);
    background: rgba(239,68,68,0.08);
  }

  /* ── Main content ── */
  .main {
    flex: 1;
    padding: 32px;
    overflow-y: auto;
    min-width: 0;
  }
  .main h2 { font-size: 28px; font-weight: 700; margin-bottom: 24px; color: #fff; display: flex; align-items: center; gap: 10px; }
  .main h2 svg { flex-shrink: 0; }
  .section-title {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 16px;
    margin-top: 36px;
    color: #fff;
  }
  .section-title:first-of-type { margin-top: 0; }

  /* ── Source Toggle ── */
  .source-toggle {
    display: flex;
    gap: 0;
    margin-bottom: 20px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    width: fit-content;
  }
  .source-toggle .toggle-btn {
    padding: 10px 24px;
    border: none;
    background: transparent;
    color: var(--text-muted);
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    font-family: inherit;
    border-right: 1px solid var(--border);
  }
  .source-toggle .toggle-btn:last-child { border-right: none; }
  .source-toggle .toggle-btn:hover { color: var(--text); background: var(--surface-2); }
  .source-toggle .toggle-btn.active {
    color: var(--accent);
    background: rgba(245,158,11,0.12);
    font-weight: 600;
  }

  /* ── Stats cards ── */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 24px;
  }
  .stat-card .label { font-size: 13px; color: var(--text-muted); font-weight: 500; margin-bottom: 6px; }
  .stat-card .value { font-size: 32px; font-weight: 700; color: #fff; font-family: 'Fira Code', 'Courier New', monospace; }
  .stat-card.accent .value { color: var(--accent); }
  .stat-card.green .value { color: var(--green); }
  .stat-card.blue .value { color: var(--blue); }
  .stat-card.pink .value { color: var(--pink); }

  /* ── Kanban ── */
  .kanban {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    margin-bottom: 32px;
  }
  .kanban-col {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    min-height: 200px;
  }
  .kanban-col .col-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
  }
  .kanban-col .col-header .count {
    background: var(--surface-2);
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 12px;
    font-family: 'Fira Code', 'Courier New', monospace;
  }
  .kanban-col .lead-card {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 8px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .kanban-col .lead-card:hover {
    border-color: var(--accent);
    transform: translateY(-1px);
  }
  .kanban-col .lead-card .lead-name {
    font-size: 14px;
    font-weight: 600;
    color: #fff;
    margin-bottom: 4px;
  }
  .kanban-col .lead-card .lead-biz {
    font-size: 12px;
    color: var(--text-muted);
  }
  .kanban-col .lead-card .lead-date {
    font-size: 11px;
    color: var(--text-muted);
    margin-top: 6px;
  }
  .kanban-col .lead-card .price-badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 600;
    color: var(--accent);
    background: rgba(245,158,11,0.12);
    padding: 2px 8px;
    border-radius: 4px;
    margin-top: 4px;
    font-family: 'Fira Code', 'Courier New', monospace;
  }
  .kanban-col.empty .col-header { opacity: 0.5; }
  .kanban-col.empty .empty-msg {
    color: var(--text-muted);
    font-size: 13px;
    text-align: center;
    padding: 24px 0;
    opacity: 0.4;
  }

  /* Pipeline column colors */
  .kanban-col[data-status="new"] { border-top: 3px solid var(--blue); }
  .kanban-col[data-status="call_scheduled"] { border-top: 3px solid var(--yellow); }
  .kanban-col[data-status="call_done"] { border-top: 3px solid var(--accent); }
  .kanban-col[data-status="building"] { border-top: 3px solid var(--pink); }
  .kanban-col[data-status="demo_ready"] { border-top: 3px solid var(--green-dim); }
  .kanban-col[data-status="delivered"] { border-top: 3px solid var(--green); }
  .kanban-col[data-status="paid"] { border-top: 3px solid #22C55E; }

  /* ── Modal ── */
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    backdrop-filter: blur(4px);
    z-index: 1000;
    align-items: center;
    justify-content: center;
  }
  .modal-overlay.open { display: flex; }
  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    width: 90%;
    max-width: 560px;
    max-height: 85vh;
    overflow-y: auto;
    padding: 32px;
    position: relative;
  }
  .modal .close-btn {
    position: absolute;
    top: 16px;
    right: 16px;
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    padding: 4px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .modal .close-btn:hover { background: var(--surface-2); color: #fff; }
  .modal h3 { font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 4px; }
  .modal .modal-sub { color: var(--text-muted); font-size: 14px; margin-bottom: 24px; }
  .modal .info-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 24px;
  }
  .modal .info-item {}
  .modal .info-item .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 2px; }
  .modal .info-item .value { font-size: 14px; color: #fff; font-weight: 500; word-break: break-word; }
  .modal .info-item.full { grid-column: 1 / -1; }
  .modal .info-item .badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 6px;
    text-transform: capitalize;
  }
  .badge-new { background: rgba(77,171,247,0.15); color: var(--blue); }
  .badge-call_scheduled { background: rgba(255,212,59,0.15); color: var(--yellow); }
  .badge-call_done { background: rgba(245,158,11,0.15); color: var(--accent); }
  .badge-building { background: rgba(255,107,157,0.15); color: var(--pink); }
  .badge-demo_ready { background: rgba(0,153,119,0.2); color: var(--green-dim); }
  .badge-delivered { background: rgba(34,197,94,0.15); color: var(--green); }
  .badge-paid { background: rgba(34,197,94,0.15); color: #22C55E; }

  .modal .form-group { margin-bottom: 16px; }
  .modal .form-group label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-muted);
    margin-bottom: 6px;
  }
  .modal .form-group select,
  .modal .form-group textarea,
  .modal .form-group input {
    width: 100%;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: #fff;
    font-size: 14px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s;
  }
  .modal .form-group select:focus,
  .modal .form-group textarea:focus,
  .modal .form-group input:focus { border-color: var(--accent); }
  .modal .form-group textarea { min-height: 80px; resize: vertical; }
  .modal .form-group select option { background: var(--surface-2); }

  .modal .btn-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 20px;
  }
  .btn {
    padding: 10px 18px;
    border: none;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    font-family: inherit;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .btn svg { flex-shrink: 0; }
  .btn-accent { background: var(--accent); color: #fff; }
  .btn-accent:hover { background: var(--accent-hover); }
  .btn-green { background: var(--green-dim); color: #fff; }
  .btn-green:hover { background: var(--green); }
  .btn-red { background: var(--red); color: #fff; }
  .btn-red:hover { opacity: 0.85; }
  .btn-outline {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
  }
  .btn-outline:hover { border-color: var(--text-muted); }
  .btn-sm { padding: 7px 14px; font-size: 12px; }

  .modal .email-section {
    border-top: 1px solid var(--border);
    padding-top: 16px;
    margin-top: 8px;
  }
  .modal .email-section .email-result {
    font-size: 13px;
    margin-top: 8px;
    padding: 8px 12px;
    border-radius: 6px;
    display: none;
  }
  .email-result.success { display: block !important; background: rgba(34,197,94,0.1); color: var(--green); }
  .email-result.error { display: block !important; background: rgba(239,68,68,0.1); color: var(--red); }

  /* ── Revenue section ── */
  .revenue-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  .revenue-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 24px;
  }
  .revenue-card .label { font-size: 13px; color: var(--text-muted); margin-bottom: 4px; }
  .revenue-card .amount { font-size: 28px; font-weight: 700; color: var(--green); font-family: 'Fira Code', 'Courier New', monospace; }

  /* ── Responsive ── */
  @media (max-width: 768px) {
    .sidebar { display: none; }
    .main { padding: 20px; }
    .stats-grid { grid-template-columns: 1fr 1fr; }
    .kanban { grid-template-columns: 1fr 1fr; }
    .revenue-grid { grid-template-columns: 1fr; }
    .modal .info-grid { grid-template-columns: 1fr; }
  }
  @media (max-width: 480px) {
    .stats-grid { grid-template-columns: 1fr; }
    .kanban { grid-template-columns: 1fr; }
    .main { padding: 16px; }
  }

  /* ── Loading ── */
  .loading { text-align: center; padding: 40px; color: var(--text-muted); font-size: 14px; }
  .loading::after { content: '...'; animation: dots 1.5s steps(4, end) infinite; }
  @keyframes dots { 0%,20% { content: ''; } 40% { content: '.'; } 60% { content: '..'; } 80%,100% { content: '...'; } }

  /* ── Toast ── */
  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    padding: 12px 20px;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 500;
    z-index: 2000;
    animation: slideIn 0.3s ease;
    display: none;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .toast svg { flex-shrink: 0; }
  /* ── Table card ── */
  .table-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  .data-table {
    width: 100%;
    border-collapse: collapse;
  }
  .data-table th {
    text-align: left;
    padding: 14px 20px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    font-weight: 600;
    border-bottom: 1px solid var(--border);
    background: var(--surface-2);
  }
  .data-table td {
    padding: 14px 20px;
    font-size: 14px;
    border-bottom: 1px solid var(--border);
    color: var(--text);
  }
  .data-table tr:last-child td { border-bottom: none; }
  .data-table tr:hover td { background: rgba(255,255,255,0.02); }
  .data-table .actions {
    display: flex;
    gap: 6px;
  }
  .data-table .actions button {
    padding: 5px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: transparent;
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
    font-family: inherit;
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .data-table .actions button svg { flex-shrink: 0; }
  .data-table .actions button:hover { border-color: var(--accent); color: var(--accent); }
  .data-table .actions button.send:hover { border-color: var(--blue); color: var(--blue); }
  .data-table .actions button.pay:hover { border-color: var(--green); color: var(--green); }
  .data-table .actions button.view-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* Invoice status badges */
  .inv-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    text-transform: capitalize;
  }
  .inv-badge.draft { background: rgba(136,136,136,0.15); color: var(--text-muted); }
  .inv-badge.sent { background: rgba(77,171,247,0.15); color: var(--blue); }
  .inv-badge.paid { background: rgba(34,197,94,0.15); color: var(--green); }
  .inv-badge.overdue { background: rgba(255,77,106,0.15); color: var(--red); }
  .inv-badge.cancelled { background: rgba(136,136,136,0.15); color: var(--text-muted); }

  .toast.show { display: flex; }
  .toast.success { background: rgba(34,197,94,0.12); border: 1px solid var(--green); color: var(--green); }
  .toast.error { background: rgba(239,68,68,0.12); border: 1px solid var(--red); color: var(--red); }
  @keyframes slideIn { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }

  /* SVG icon sizing */
  .icon { width: 18px; height: 18px; }
  .icon-sm { width: 14px; height: 14px; }
  .icon-inline { display: inline-block; vertical-align: middle; position: relative; top: -1px; }
</style>
</head>
<body>

<!-- Hidden SVG sprite -->
<svg style="display:none" aria-hidden="true">
  <defs>
    <g id="ic-dashboard"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></g>
    <g id="ic-file"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></g>
    <g id="ic-dollar"><line x1="12" y1="2" x2="12" y2="22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></g>
    <g id="ic-columns"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="12" y1="3" x2="12" y2="21"/></g>
    <g id="ic-logout"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></g>
    <g id="ic-x"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></g>
    <g id="ic-briefcase"><rect x="2" y="7" width="20" height="13" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/></g>
    <g id="ic-refresh"><polyline points="21 2 21 8 15 8"/><polyline points="3 22 3 16 9 16"/><path d="M21 12A9 9 0 0 0 5.64 5.64L3 8"/><path d="M3 12a9 9 0 0 0 15.36 6.36L21 16"/></g>
    <g id="ic-clipboard"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/></g>
    <g id="ic-settings"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></g>
    <g id="ic-sparkles"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></g>
    <g id="ic-calendar"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></g>
    <g id="ic-check-circle"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></g>
    <g id="ic-wrench"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></g>
    <g id="ic-play-circle"><circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/></g>
    <g id="ic-package"><path d="m16 16 2 2 4-4"/><path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l2-1.14"/><polyline points="3.29 7 12 12 20.71 7"/><line x1="12" y1="22" x2="12" y2="12"/></g>
    <g id="ic-dollar-circle"><circle cx="12" cy="12" r="10"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><line x1="12" y1="6" x2="12" y2="18"/></g>
    <g id="ic-rocket"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/></g>
    <g id="ic-bot"><rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 5V3a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><line x1="8" y1="13" x2="8" y2="13"/><line x1="12" y1="13" x2="12" y2="13"/><line x1="16" y1="13" x2="16" y2="13"/></g>
    <g id="ic-file-plus"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/></g>
    <g id="ic-link"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></g>
    <g id="ic-clipboard-copy"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/><rect x="7" y="10" width="10" height="10" rx="2"/></g>
    <g id="ic-mail"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 4-10 8L2 4"/></g>
    <g id="ic-eye"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></g>
    <g id="ic-ban"><circle cx="12" cy="12" r="10"/><path d="m4.93 4.93 14.14 14.14"/></g>
    <g id="ic-circle"><circle cx="12" cy="12" r="10"/></g>
  </defs>
</svg>

<!-- Sidebar -->
<div class="sidebar">
  <div class="brand">Automate Pro</div>
  <nav>
    <a href="#" class="active" onclick="switchTab('dashboard'); return false;">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-dashboard"/></svg>
      Dashboard
    </a>
    <a href="#" onclick="switchTab('invoices'); return false;">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-file"/></svg>
      Invoices
    </a>
    <a href="#" onclick="switchTab('revenue'); return false;">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-dollar"/></svg>
      Revenue
    </a>
    <a href="#" onclick="switchTab('pipeline'); return false;">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-columns"/></svg>
      Pipeline
    </a>
  </nav>
  <div class="logout-section">
    <button class="logout-btn" onclick="logout()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-logout"/></svg>
      Sign Out
    </button>
  </div>
</div>

<!-- Main content -->
<div class="main" id="main-content">
  <h2>
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-dashboard"/></svg>
    Dashboard
  </h2>
  <div class="source-toggle" id="source-toggle">
    <button class="toggle-btn active" data-filter="my" onclick="setSourceFilter('my')">My Leads</button>
    <button class="toggle-btn" data-filter="all" onclick="setSourceFilter('all')">All Clients</button>
  </div>
  <div class="stats-grid" id="stats-grid">
    <div class="stat-card"><div class="label">Total Leads</div><div class="value" id="stat-total">—</div></div>
    <div class="stat-card green"><div class="label">New This Week</div><div class="value" id="stat-week">—</div></div>
    <div class="stat-card accent"><div class="label">Revenue This Month</div><div class="value" id="stat-revenue">$0</div></div>
    <div class="stat-card blue"><div class="label">Conversion Rate</div><div class="value" id="stat-conversion">0%</div></div>
    <div class="stat-card pink"><div class="label">Pending Delivery</div><div class="value" id="stat-pending">0</div></div>
    <div class="stat-card" style="border-top:3px solid var(--green-dim);"><div class="label">Active Clients</div><div class="value" id="stat-active">0</div></div>
  </div>

  <div class="section-title">Pipeline</div>
  <div class="kanban" id="kanban"></div>

  <div class="section-title" id="revenue-title" style="display:none;">Revenue Overview</div>
  <div class="revenue-grid" id="revenue-grid" style="display:none;">
    <div class="revenue-card"><div class="label">Total Revenue (Closed Won)</div><div class="amount" id="rev-total">$0</div></div>
    <div class="revenue-card"><div class="label">This Month</div><div class="amount" id="rev-month">$0</div></div>
  </div>

  <!-- ── Invoices Tab ── -->
  <div id="invoices-tab" style="display:none;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;">
      <h2 style="margin-bottom:0;">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-file"/></svg>
        Invoices
      </h2>
      <button class="btn btn-accent" onclick="openCreateInvoiceModal()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-file-plus"/></svg>
        Create Invoice
      </button>
    </div>
    <div class="table-card">
      <table class="data-table" id="invoices-table">
        <thead>
          <tr>
            <th>Invoice #</th>
            <th>Client</th>
            <th>Setup</th>
            <th>Subscription</th>
            <th>Status</th>
            <th>Date</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="invoices-body">
          <tr><td colspan="7" class="loading">Loading invoices</td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div>

<!-- Create Invoice Modal -->
<div class="modal-overlay" id="create-invoice-modal">
  <div class="modal">
    <button class="close-btn" onclick="closeCreateInvoiceModal()">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-x"/></svg>
    </button>
    <h3>Create Invoice</h3>
    <div class="modal-sub">Generate a new invoice for a client</div>

    <!-- One-Time Setup Fee -->
    <div style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;display:flex;align-items:center;gap:6px;">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-briefcase"/></svg>
      One-Time Setup Fee
    </div>
    <div class="form-group">
      <label>Client Name</label>
      <input type="text" id="inv-client-name" placeholder="Full name">
    </div>
    <div class="form-group">
      <label>Client Email</label>
      <input type="email" id="inv-client-email" placeholder="client@example.com">
    </div>
    <div class="form-group">
      <label>Setup Amount ($)</label>
      <input type="number" id="inv-amount" placeholder="0.00" step="0.01" min="0">
    </div>
    <div class="form-group">
      <label>Description</label>
      <input type="text" id="inv-description" placeholder="e.g. Automation Setup">
    </div>
    <div class="form-group">
      <label>Due Date (optional)</label>
      <input type="date" id="inv-due-date">
    </div>

    <!-- Subscription Toggle -->
    <div style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin:20px 0 12px;display:flex;align-items:center;gap:6px;">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-refresh"/></svg>
      Subscription (Optional)
    </div>
    <div class="form-group" style="display:flex;align-items:center;gap:10px;">
      <label style="margin-bottom:0;">Add recurring subscription?</label>
      <label class="toggle-switch">
        <input type="checkbox" id="inv-has-sub" onchange="toggleSubFields()">
        <span class="toggle-slider"></span>
      </label>
    </div>
    <div id="inv-sub-fields" style="display:none;">
      <div class="form-group">
        <label>Subscription Amount ($)</label>
        <input type="number" id="inv-sub-amount" placeholder="0.00" step="0.01" min="0">
      </div>
      <div class="form-group">
        <label>Billing Interval</label>
        <select id="inv-sub-interval">
          <option value="month">Monthly</option>
          <option value="year">Yearly</option>
          <option value="week">Weekly</option>
        </select>
      </div>
      <div class="form-group">
        <label>Description (e.g. "Monthly Retainer")</label>
        <input type="text" id="inv-sub-desc" placeholder="Monthly Retainer">
      </div>
    </div>

    <div class="form-group" id="inv-lead-group" style="display:none;">
      <label>Lead ID</label>
      <input type="text" id="inv-lead-id" readonly style="opacity:0.6;font-size:12px;">
    </div>
    <div class="btn-row" style="margin-bottom:0;">
      <button class="btn btn-accent" onclick="createInvoice()">Generate Invoice</button>
      <button class="btn btn-green" onclick="createAndSendInvoice()" style="background:#22C55E;color:#0F172A;border:none;padding:10px 20px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;">Create &amp; Send</button>
      <button class="btn btn-outline" onclick="closeCreateInvoiceModal()">Cancel</button>
    </div>
    <div class="email-result" id="create-invoice-result"></div>
  </div>
</div>

<style>
.toggle-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
}
.toggle-switch input { opacity: 0; width: 0; height: 0; }
.toggle-slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 24px;
  transition: 0.2s;
}
.toggle-slider:before {
  content: "";
  position: absolute;
  height: 18px;
  width: 18px;
  left: 2px;
  bottom: 2px;
  background: var(--text-muted);
  border-radius: 50%;
  transition: 0.2s;
}
.toggle-switch input:checked + .toggle-slider {
  background: rgba(34,197,94,0.2);
  border-color: var(--green);
}
.toggle-switch input:checked + .toggle-slider:before {
  transform: translateX(20px);
  background: var(--green);
}
</style>

<!-- Confirm Invoice Modal -->
<div class="modal-overlay" id="confirm-invoice-modal">
  <div class="modal" style="max-width:420px;">
    <button class="close-btn" onclick="document.getElementById('confirm-invoice-modal').classList.remove('open')">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-x"/></svg>
    </button>
    <h3 id="confirm-title">Confirm</h3>
    <p style="color:#888;font-size:14px;margin:8px 0 24px;" id="confirm-msg"></p>
    <div class="btn-row" style="margin-bottom:0;">
      <button class="btn btn-accent" id="confirm-yes-btn" onclick="">Yes</button>
      <button class="btn btn-outline" onclick="document.getElementById('confirm-invoice-modal').classList.remove('open')">Cancel</button>
    </div>
  </div>
</div>

<!-- Lead Detail Modal -->
<div class="modal-overlay" id="modal">
  <div class="modal">
    <button class="close-btn" onclick="closeModal()">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-x"/></svg>
    </button>
    <h3 id="modal-name">—</h3>
    <div class="modal-sub" id="modal-email">—</div>

    <!-- Section 1: Lead Info (Read-only) -->
    <div style="margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--border);">
      <div style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;display:flex;align-items:center;gap:6px;">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-clipboard"/></svg>
        Lead Info
      </div>
      <div class="info-grid">
        <div class="info-item"><div class="label">Business Type</div><div class="value" id="modal-biz">—</div></div>
        <div class="info-item"><div class="label">Source</div><div class="value" id="modal-source">—</div></div>
        <div class="info-item"><div class="label">Status</div><div class="value"><span class="badge" id="modal-status-badge">new</span></div></div>
        <div class="info-item"><div class="label">Phone</div><div class="value" id="modal-phone">—</div></div>
        <div class="info-item full"><div class="label">Message</div><div class="value" id="modal-message" style="font-weight:400;">—</div></div>
        <div class="info-item full"><div class="label">Created</div><div class="value" id="modal-created" style="font-weight:400;">—</div></div>
      </div>
    </div>

    <!-- Section 2: Workflow (Editable) -->
    <div style="margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--border);">
      <div style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;display:flex;align-items:center;gap:6px;">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-settings"/></svg>
        Workflow
      </div>
      <div class="form-group">
        <label>Status</label>
        <select id="modal-status-select" onchange="updateLeadStatus()">
          <option value="new">New</option>
          <option value="call_scheduled">Call Scheduled</option>
          <option value="call_done">Call Done</option>
          <option value="building">Building</option>
          <option value="demo_ready">Demo Ready</option>
          <option value="delivered">Delivered</option>
          <option value="paid">Paid</option>
        </select>
      </div>
      <div class="form-group">
        <label>Requirements</label>
        <textarea id="modal-requirements" placeholder="What they need built..." onchange="updateRequirements()"></textarea>
      </div>
      <div class="form-group">
        <label>Quoted Price ($)</label>
        <input type="number" id="modal-quoted-price" placeholder="0.00" step="0.01" min="0" onchange="updateQuotedPrice()">
      </div>
      <div class="form-group">
        <label>Follow-up Date</label>
        <input type="date" id="modal-follow-up" onchange="updateFollowUp()">
      </div>
      <div class="form-group">
        <label>Notes</label>
        <textarea id="modal-notes" placeholder="Add notes about this lead..." onchange="updateNotes()"></textarea>
      </div>
    </div>

    <!-- Section 3: Quick Actions -->
    <div style="margin-bottom:16px;">
      <div style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;display:flex;align-items:center;gap:6px;">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-sparkles"/></svg>
        Quick Actions
      </div>
      <div class="btn-row">
        <button class="btn btn-build btn-sm" onclick="buildNow()" style="background:#22C55E;color:#0F172A;border:none;padding:8px 14px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-bot"/></svg>
          Build Now
        <button class="btn btn-green btn-sm" onclick="createInvoiceForLead()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-file-plus"/></svg>
          Create Invoice</button>
        <button class="btn btn-accent btn-sm" onclick="copyCalendlyLink()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-link"/></svg>
          Copy Calendly Link</button>
        <button class="btn btn-outline btn-sm" onclick="copyRequirementsSummary()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-clipboard-copy"/></svg>
          Copy Requirements</button>
      </div>
      <div class="email-section">
        <div class="form-group" style="margin-top:12px;">
          <label>Send Email to Lead</label>
          <input type="text" id="email-subject" placeholder="Subject" style="margin-bottom:8px;">
          <textarea id="email-body" placeholder="Email body..." style="min-height:80px;"></textarea>
        </div>
        <button class="btn btn-outline btn-sm" onclick="sendEmailToLead()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-mail"/></svg>
          Send Email</button>
        <div class="email-result" id="email-result"></div>
      </div>
    </div>
  </div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
let currentLeadId = null;
let currentFilter = 'my';
const STATUS_LABELS = {
  'new': 'New',
  'call_scheduled': 'Call Scheduled',
  'call_done': 'Call Done',
  'building': 'Building',
  'demo_ready': 'Demo Ready',
  'delivered': 'Delivered',
  'paid': 'Paid'
};
const STATUS_COLUMNS = ['new', 'call_scheduled', 'call_done', 'building', 'demo_ready', 'delivered', 'paid'];

function $(id) { return document.getElementById(id); }

function toast(msg, type) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  clearTimeout(t._timeout);
  t._timeout = setTimeout(() => { t.className = 'toast'; }, 3500);
}

function formatDate(d) {
  if (!d) return '—';
  try {
    const dt = new Date(d);
    return dt.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
  } catch { return d; }
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, { credentials: 'same-origin', ...opts });
  if (res.redirected && res.url.includes('/login')) {
    window.location.href = '/login';
    return null;
  }
  return await res.json();
}

// ── Dashboard loading ──
function setSourceFilter(mode) {
  currentFilter = mode;
  document.querySelectorAll('.source-toggle .toggle-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === mode);
  });
  loadDashboard();
}

async function loadDashboard() {
  const url = '/api/dashboard' + (currentFilter === 'all' ? '?include_all=true' : '');
  const data = await fetchJSON(url);
  if (!data) return;

  $('stat-total').textContent = data.total_leads;
  $('stat-week').textContent = data.new_this_week;
  $('stat-revenue').textContent = '$' + Number(data.revenue_this_month).toLocaleString('en-AU', { minimumFractionDigits: 0 });
  $('stat-conversion').textContent = data.conversion_rate + '%';
  $('stat-pending').textContent = data.pending_delivery || 0;
  $('stat-active').textContent = data.active_clients || 0;

  $('rev-total').textContent = '$' + Number(data.total_revenue).toLocaleString('en-AU', { minimumFractionDigits: 2 });
  $('rev-month').textContent = '$' + Number(data.revenue_this_month).toLocaleString('en-AU', { minimumFractionDigits: 2 });

  renderKanban(data.leads_by_status, data.recent_leads);
}

async function loadAllLeadsForKanban() {
  const leads = await fetchJSON('/api/leads');
  if (!leads) return;
  renderKanbanFromLeads(leads);
}

function renderKanban(statusCounts, recentLeads) {
  // Use recent leads if we have them, otherwise fetch all
  if (recentLeads && recentLeads.length > 0) {
    renderKanbanFromLeads(recentLeads);
  } else {
    loadAllLeadsForKanban();
  }
}

async function renderKanbanFromLeads(leads) {
  // If we only got 5 recent leads, fetch all for full pipeline
  if (leads.length <= 5) {
    const all = await fetchJSON('/api/leads');
    if (all) leads = all;
  }

  const groups = {};
  for (const s of STATUS_COLUMNS) groups[s] = [];
  for (const lead of leads) {
    const status = lead.status || 'new';
    if (groups[status]) groups[status].push(lead);
  }

  const kanban = $('kanban');
  kanban.innerHTML = '';
  for (const col of STATUS_COLUMNS) {
    const colLeads = groups[col] || [];
    const colDiv = document.createElement('div');
    colDiv.className = 'kanban-col' + (colLeads.length === 0 ? ' empty' : '');
    colDiv.dataset.status = col;
    colDiv.innerHTML = `
      <div class="col-header">
        <span>${STATUS_LABELS[col]}</span>
        <span class="count">${colLeads.length}</span>
      </div>
      ${colLeads.length === 0 ? '<div class="empty-msg">No leads</div>' : ''}
      <div class="col-cards"></div>
    `;
    const cardsContainer = colDiv.querySelector('.col-cards');
    for (const lead of colLeads) {
      const card = document.createElement('div');
      card.className = 'lead-card';
      card.dataset.id = lead.id;
      card.innerHTML = `
        <div class="lead-name">${escapeHtml(lead.name)}</div>
        <div class="lead-biz">${escapeHtml(lead.business_type || '—')}</div>
        <div class="lead-date">${formatDate(lead.created_at)}</div>
        ${lead.quoted_price > 0 ? '<div class="price-badge">Quoted: $' + Number(lead.quoted_price).toLocaleString() + '</div>' : ''}
      `;
      card.onclick = () => openLeadDetail(lead.id);
      cardsContainer.appendChild(card);
    }
    kanban.appendChild(colDiv);
  }
}

function escapeHtml(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── Lead Detail Modal ──
async function openLeadDetail(id) {
  currentLeadId = id;
  const lead = await fetchJSON('/api/lead/' + id);
  if (!lead) return;

  $('modal-name').textContent = lead.name;
  $('modal-email').textContent = lead.email;
  $('modal-biz').textContent = lead.business_type || '—';
  $('modal-phone').textContent = lead.phone || '—';
  $('modal-source').textContent = lead.source || 'website';
  $('modal-message').textContent = lead.message || '—';
  $('modal-created').textContent = formatDate(lead.created_at);

  const badge = $('modal-status-badge');
  badge.textContent = STATUS_LABELS[lead.status] || 'New';
  badge.className = 'badge badge-' + (lead.status || 'new');

  // Workflow fields
  $('modal-status-select').value = lead.status || 'new';
  $('modal-requirements').value = lead.requirements || '';
  $('modal-quoted-price').value = lead.quoted_price || '';
  $('modal-follow-up').value = lead.follow_up_date || '';
  $('modal-notes').value = lead.notes || '';

  // Email fields
  $('email-subject').value = '';
  $('email-body').value = '';
  $('email-result').className = 'email-result';
  $('email-result').textContent = '';

  $('modal').classList.add('open');
}

function closeModal() {
  $('modal').classList.remove('open');
  currentLeadId = null;
}

async function updateLeadStatus() {
  const status = $('modal-status-select').value;
  await updateField('status', status);
  const badge = $('modal-status-badge');
  badge.textContent = STATUS_LABELS[status];
  badge.className = 'badge badge-' + status;
  toast('Status updated to ' + STATUS_LABELS[status], 'success');
  loadDashboard();
}

async function updateNotes() {
  const notes = $('modal-notes').value;
  await updateField('notes', notes);
  toast('Notes saved', 'success');
}

async function updateRequirements() {
  const req = $('modal-requirements').value;
  await updateField('requirements', req);
  toast('Requirements saved', 'success');
}

async function updateQuotedPrice() {
  const price = parseFloat($('modal-quoted-price').value) || 0;
  await updateField('quoted_price', price);
  toast('Price quoted: $' + price.toFixed(2), 'success');
}

async function updateFollowUp() {
  const date = $('modal-follow-up').value;
  await updateField('follow_up_date', date);
  if (date) {
    toast('Follow-up set for ' + formatDate(date), 'success');
  }
}

async function updateField(key, value) {
  if (!currentLeadId) return;
  await fetchJSON('/api/lead/' + currentLeadId, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [key]: value })
  });
}

// ── Quick Actions ──
function buildNow() {
  const modal = document.querySelector('.ll-modal-overlay.show');
  if (!modal) return;
  const reqEl = document.getElementById('lead-requirements');
  const priceEl = document.getElementById('lead-quoted-price');
  const nameEl = document.querySelector('.ll-lead-name');
  const name = nameEl ? nameEl.textContent || 'Unknown' : 'Unknown';
  const req = reqEl ? reqEl.value || 'No requirements specified' : 'No requirements';
  const price = priceEl ? priceEl.value || 'Not set' : 'Not set';
  const text = 'Client: ' + name + '\\nRequirements: ' + req + '\\nBudget: $' + price;
  navigator.clipboard.writeText(text).then(() => {
    toast('Copied! Now tell Jarvis: "Build this automation"', 'success');
  }).catch(() => {
    prompt('Copy this text and send to Jarvis:', text);
  });
}

function copyCalendlyLink() {
  const link = 'https://calendly.com/emilio-pegolo1/30min';
  navigator.clipboard.writeText(link).then(() => {
    toast('Calendly link copied!', 'success');
  }).catch(() => {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = link;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast('Calendly link copied!', 'success');
  });
}

function copyRequirementsSummary() {
  if (!currentLeadId) return;
  const name = $('modal-name').textContent;
  const biz = $('modal-biz').textContent;
  const req = $('modal-requirements').value || 'Not specified';
  const price = $('modal-quoted-price').value || 'Not quoted';
  const summary = `Client: ${name}\nBusiness: ${biz}\nRequirements: ${req}\nQuoted Price: $${price}\n---\nGenerated by Automate Pro`;
  navigator.clipboard.writeText(summary).then(() => {
    toast('Requirements summary copied!', 'success');
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = summary;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast('Requirements summary copied!', 'success');
  });
}

async function sendEmailToLead() {
  if (!currentLeadId) return;
  const subject = $('email-subject').value.trim();
  const body = $('email-body').value.trim();
  const resultDiv = $('email-result');

  if (!subject || !body) {
    resultDiv.textContent = 'Please enter both subject and body.';
    resultDiv.className = 'email-result error';
    return;
  }

  resultDiv.textContent = 'Sending...';
  resultDiv.className = 'email-result';
  resultDiv.style.display = 'block';

  const res = await fetchJSON('/api/lead/' + currentLeadId + '/send-email', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subject, body })
  });

  if (res && res.success) {
    resultDiv.textContent = res.message;
    resultDiv.className = 'email-result success';
    toast('Email sent!', 'success');
  } else {
    resultDiv.textContent = (res ? res.error : 'Failed to send');
    resultDiv.className = 'email-result error';
  }
}

// ── Invoice Functions ──

function toggleSubFields() {
  const checked = $('inv-has-sub').checked;
  $('inv-sub-fields').style.display = checked ? 'block' : 'none';
}

async function loadInvoices() {
  const invs = await fetchJSON('/api/invoices');
  if (!invs) return;
  const tbody = $('invoices-body');
  if (invs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888;padding:40px;">No invoices yet. Create your first one!</td></tr>';
    return;
  }
  tbody.innerHTML = invs.map(inv => {
    const statusClass = inv.status || 'draft';
    const dateStr = inv.created_at ? inv.created_at.slice(0, 10) : '—';
    const leadAttr = inv.lead_id ? `data-lead="${inv.lead_id}"` : '';
    const hasSub = inv.has_subscription;
    const subInfo = hasSub ? `$${Number(inv.sub_amount||0).toLocaleString('en-AU',{minimumFractionDigits:2})}/${inv.sub_interval||'month'}` : '—';
    const subBadge = hasSub ? (inv.sub_status === 'active' ? '<span style="color:var(--green);font-size:11px;">active</span>' : inv.sub_status === 'cancelled' ? '<span style="color:var(--red);font-size:11px;">cancelled</span>' : '<span style="color:var(--blue);font-size:11px;">pending</span>') : '';
    return `<tr ${leadAttr}>
      <td style="font-weight:600;color:#fff;">${escapeHtml(inv.invoice_number)}</td>
      <td>
        <div style="font-weight:500;">${escapeHtml(inv.client_name)}</div>
        <div style="font-size:12px;color:#888;">${escapeHtml(inv.client_email)}</div>
      </td>
      <td style="font-weight:600;color:#fff;">$${Number(inv.amount).toLocaleString('en-AU', {minimumFractionDigits:2})}</td>
      <td>
        <div style="font-size:13px;">${subInfo}</div>
        <div>${subBadge}</div>
      </td>
      <td><span class="inv-badge ${statusClass}">${statusClass}</span></td>
      <td style="color:#888;font-size:13px;">${dateStr}</td>
      <td>
        <div class="actions">
          <button class="view-btn" onclick="window.open('/invoice/${inv.id}','_blank')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-eye"/></svg> View</button>
          ${statusClass !== 'paid' && statusClass !== 'cancelled' ? `<button class="send" onclick="sendInvoice('${inv.id}')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-mail"/></svg> Send</button>` : ''}
          ${statusClass === 'sent' ? `<button class="pay" onclick="markInvoicePaid('${inv.id}')"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-check-circle"/></svg> Paid</button>` : ''}
          ${hasSub && inv.sub_status === 'active' ? `<button class="cancel-sub" onclick="cancelSubscription('${inv.id}')" style="color:var(--red);border-color:var(--red);"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><use href="#ic-ban"/></svg> Cancel Sub</button>` : ''}
        </div>
      </td>
    </tr>`;
  }).join('');
}

async function sendInvoice(id) {
  const res = await fetchJSON('/api/invoices/' + id + '/send', { method: 'POST' });
  if (res && res.success) {
    toast(res.message, 'success');
    loadInvoices();
  } else {
    toast((res ? res.error : 'Failed to send'), 'error');
  }
}

async function markInvoicePaid(id) {
  const res = await fetchJSON('/api/invoices/' + id, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'paid' })
  });
  if (res) {
    toast('Invoice marked as paid', 'success');
    loadInvoices();
  } else {
    toast('Failed to update invoice', 'error');
  }
}

async function cancelSubscription(id) {
  if (!confirm(`Are you sure you want to cancel this subscription?\n\nThis will:\n- Cancel the recurring billing in Stripe\n- Email Emilio to take down the client's website\n- Mark the subscription as cancelled`)) return;
  const res = await fetchJSON('/api/invoices/' + id + '/cancel-subscription', { method: 'POST' });
  if (res && res.success) {
    toast(res.message, 'success');
    loadInvoices();
  } else {
    toast((res ? res.error : 'Failed to cancel subscription'), 'error');
  }
}

function openCreateInvoiceModal() {
  $('inv-client-name').value = '';
  $('inv-client-email').value = '';
  $('inv-amount').value = '';
  $('inv-description').value = '';
  $('inv-due-date').value = '';
  $('inv-lead-id').value = '';
  $('inv-lead-group').style.display = 'none';
  $('inv-has-sub').checked = false;
  $('inv-sub-fields').style.display = 'none';
  $('inv-sub-amount').value = '';
  $('inv-sub-interval').value = 'month';
  $('inv-sub-desc').value = '';
  $('create-invoice-result').className = 'email-result';
  $('create-invoice-result').textContent = '';
  $('create-invoice-modal').classList.add('open');
}

function closeCreateInvoiceModal() {
  $('create-invoice-modal').classList.remove('open');
}

function createInvoiceForLead() {
  if (!currentLeadId) return;
  fetchJSON('/api/lead/' + currentLeadId).then(lead => {
    if (!lead) return;
    $('inv-client-name').value = lead.name || '';
    $('inv-client-email').value = lead.email || '';
    $('inv-amount').value = lead.quoted_price > 0 ? lead.quoted_price : '';
    $('inv-description').value = (lead.requirements || '') + ' - Automation Service';
    $('inv-due-date').value = lead.follow_up_date || '';
    $('inv-lead-id').value = currentLeadId;
    $('inv-lead-group').style.display = 'block';
    $('inv-has-sub').checked = false;
    $('inv-sub-fields').style.display = 'none';
    $('inv-sub-amount').value = '';
    $('inv-sub-interval').value = 'month';
    $('inv-sub-desc').value = '';
    $('create-invoice-result').className = 'email-result';
    $('create-invoice-result').textContent = '';
    $('create-invoice-modal').classList.add('open');
    closeModal();
  });
}

async function createInvoice() {
  const name = $('inv-client-name').value.trim();
  const email = $('inv-client-email').value.trim();
  const amount = parseFloat($('inv-amount').value);
  const desc = $('inv-description').value.trim();
  const due = $('inv-due-date').value;
  const leadId = $('inv-lead-id').value;
  const hasSub = $('inv-has-sub').checked;
  const resultDiv = $('create-invoice-result');

  if (!name || !email) {
    resultDiv.textContent = 'Please enter client name and email.';
    resultDiv.className = 'email-result error';
    return;
  }
  if (!amount || amount <= 0) {
    resultDiv.textContent = 'Please enter a valid setup amount.';
    resultDiv.className = 'email-result error';
    return;
  }

  resultDiv.textContent = 'Creating invoice...';
  resultDiv.className = 'email-result';
  resultDiv.style.display = 'block';

  const body = {
    client_name: name,
    client_email: email,
    amount: amount,
    description: desc,
    due_date: due,
    has_subscription: hasSub,
    sub_amount: hasSub ? parseFloat($('inv-sub-amount').value) || 0 : 0,
    sub_interval: hasSub ? ($('inv-sub-interval').value || 'month') : 'month',
    sub_description: hasSub ? ($('inv-sub-desc').value.trim() || '') : ''
  };
  if (leadId) body.lead_id = leadId;

  const res = await fetchJSON('/api/invoices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  if (res && !res.error) {
    resultDiv.textContent = 'Invoice ' + (res.invoice_number || '') + ' created!';
    resultDiv.className = 'email-result success';
    toast('Invoice ' + (res.invoice_number || '') + ' created!', 'success');
    closeCreateInvoiceModal();
    loadInvoices();
  } else {
    resultDiv.textContent = (res ? res.error : 'Failed to create invoice');
    resultDiv.className = 'email-result error';
  }
}

async function createAndSendInvoice() {
  const name = $('inv-client-name').value.trim();
  const email = $('inv-client-email').value.trim();
  const amount = parseFloat($('inv-amount').value);
  const desc = $('inv-description').value.trim();
  const due = $('inv-due-date').value;
  const hasSub = $('inv-has-sub').checked;
  const resultDiv = $('create-invoice-result');
  if (!name || !email) { resultDiv.textContent = 'Please fill in name and email'; resultDiv.className = 'email-result error'; return; }
  if (!amount || amount <= 0) { resultDiv.textContent = 'Invalid setup amount'; resultDiv.className = 'email-result error'; return; }
  resultDiv.textContent = 'Creating and sending...'; resultDiv.className = 'email-result'; resultDiv.style.display = 'block';
  const body = {
    client_name: name, client_email: email, amount: amount,
    description: desc, due_date: due,
    has_subscription: hasSub,
    sub_amount: hasSub ? parseFloat($('inv-sub-amount').value) || 0 : 0,
    sub_interval: hasSub ? ($('inv-sub-interval').value || 'month') : 'month',
    sub_description: hasSub ? ($('inv-sub-desc').value.trim() || '') : ''
  };
  const res = await fetchJSON('/api/invoices', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
  if (res && !res.error) {
    resultDiv.textContent = 'Invoice created! Now sending...';
    const sendResult = await fetchJSON('/api/invoices/' + res.id + '/send', { method:'POST' });
    if (sendResult && sendResult.success) {
      resultDiv.textContent = 'Invoice sent! ' + (hasSub ? 'Setup + subscription links sent!' : 'Stripe payment link sent!');
      resultDiv.className = 'email-result success';
      toast('Invoice created and sent!', 'success');
      closeCreateInvoiceModal();
      loadInvoices();
    } else {
      resultDiv.textContent = 'Invoice created but send failed: ' + (sendResult ? sendResult.error : 'Unknown');
      resultDiv.className = 'email-result error';
      closeCreateInvoiceModal();
      loadInvoices();
    }
  } else {
    resultDiv.textContent = (res ? res.error : 'Failed');
    resultDiv.className = 'email-result error';
  }
}


// ── Tab switching ──
function switchTab(tab) {
  document.querySelectorAll('.sidebar nav a').forEach(a => a.classList.remove('active'));
  event.target.classList.add('active');

  // Hide all non-sidebar panels first
  $('invoices-tab').style.display = 'none';
  document.querySelectorAll('.section-title, #revenue-title').forEach(el => el.style.display = 'none');
  $('main-content').querySelector('h2').style.display = 'block';

  if (tab === 'dashboard') {
    $('main-content').querySelector('h2').textContent = 'Dashboard';
    $('stats-grid').style.display = 'grid';
    document.querySelector('.section-title').textContent = 'Pipeline';
    document.querySelector('.section-title').style.display = 'block';
    $('kanban').style.display = 'grid';
    $('revenue-title').style.display = 'none';
    $('revenue-grid').style.display = 'none';
    loadDashboard();
  } else if (tab === 'revenue') {
    $('main-content').querySelector('h2').textContent = 'Revenue';
    $('stats-grid').style.display = 'none';
    document.querySelector('.section-title').style.display = 'none';
    $('kanban').style.display = 'none';
    $('revenue-title').style.display = 'block';
    $('revenue-grid').style.display = 'grid';
    loadDashboard();
  } else if (tab === 'invoices') {
    $('main-content').querySelector('h2').style.display = 'none';
    $('stats-grid').style.display = 'none';
    document.querySelector('.section-title').style.display = 'none';
    $('kanban').style.display = 'none';
    $('revenue-title').style.display = 'none';
    $('revenue-grid').style.display = 'none';
    $('invoices-tab').style.display = 'block';
    loadInvoices();
  } else if (tab === 'pipeline') {
    $('main-content').querySelector('h2').textContent = 'Pipeline';
    $('main-content').querySelector('h2').style.display = 'block';
    $('invoices-tab').style.display = 'none';
    $('stats-grid').style.display = 'none';
    document.querySelector('.section-title').textContent = 'Full Pipeline';
    document.querySelector('.section-title').style.display = 'block';
    $('kanban').style.display = 'grid';
    $('revenue-title').style.display = 'none';
    $('revenue-grid').style.display = 'none';
    loadAllLeadsForKanban();
  }
}

async function logout() {
  await fetchJSON('/logout', { method: 'POST' });
  window.location.href = '/login';
}

// Click outside modal to close
$('modal').onclick = function(e) {
  if (e.target === this) closeModal();
};
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeModal();
});

// ── Init ──
$('invoices-tab').style.display = 'none';
loadDashboard();
</script>
</body>
</html>"""


# ── Main ─────────────────────────────────────────────────────────────────────



@app.route("/api/test-email")
def api_test_email():
    """Test email sending via Gmail API."""
    result = {
        "gmail_configured": bool(GMAIL_REFRESH_TOKEN and GMAIL_CLIENT_ID),
        "send_test": send_email("emilio.pegolo1@gmail.com", "Railway Email Test", "If you see this, emails are working again!")
    }
    return jsonify(result)

# ── Plumber Demo Page ──────────────────────────────────────────────────

PLUMBER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bob's Plumbing Services — Sydney's Trusted Plumber</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #0a0a0f;
    --surface: #12121a;
    --surface-2: #1a1a26;
    --border: #2a2a3a;
    --text: #e0e0e0;
    --text-muted: #888;
    --accent: #ff8c42;
    --accent-hover: #e07a30;
    --accent-dim: rgba(255,140,66,0.1);
    --green: #00d4aa;
    --blue: #4dabf7;
  }
  html { scroll-behavior: smooth; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }

  /* ── Navigation ── */
  nav {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 100;
    background: rgba(10,10,15,0.95);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  nav .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  nav .brand-icon {
    width: 36px;
    height: 36px;
    background: var(--accent);
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    color: #fff;
  }
  nav .brand-text {
    font-size: 18px;
    font-weight: 700;
    color: #fff;
  }
  nav .brand-text span { color: var(--accent); }
  nav .nav-links {
    display: flex;
    gap: 24px;
    list-style: none;
  }
  nav .nav-links a {
    color: var(--text-muted);
    text-decoration: none;
    font-size: 14px;
    font-weight: 500;
    transition: color 0.2s;
  }
  nav .nav-links a:hover { color: var(--accent); }
  nav .nav-cta {
    display: inline-block;
    padding: 10px 22px;
    background: var(--accent);
    color: #0a0a0f;
    font-weight: 600;
    font-size: 14px;
    border-radius: 8px;
    text-decoration: none;
    transition: background 0.2s;
  }
  nav .nav-cta:hover { background: var(--accent-hover); }
  .mobile-toggle {
    display: none;
    background: none;
    border: none;
    color: var(--text);
    font-size: 24px;
    cursor: pointer;
    padding: 4px;
  }

  /* ── Hero ── */
  .hero {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 120px 24px 80px;
    position: relative;
    overflow: hidden;
  }
  .hero::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 50% 0%, rgba(255,140,66,0.08) 0%, transparent 60%),
                radial-gradient(ellipse at 80% 20%, rgba(34,197,94,0.04) 0%, transparent 50%);
    pointer-events: none;
  }
  .hero-content { position: relative; z-index: 1; max-width: 800px; }
  .hero-badge {
    display: inline-block;
    padding: 6px 16px;
    border-radius: 20px;
    background: var(--accent-dim);
    border: 1px solid rgba(255,140,66,0.2);
    color: var(--accent);
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 20px;
  }
  .hero h1 {
    font-size: clamp(36px, 6vw, 64px);
    font-weight: 800;
    color: #fff;
    line-height: 1.15;
    margin-bottom: 20px;
  }
  .hero h1 span { color: var(--accent); }
  .hero p {
    font-size: 18px;
    color: var(--text-muted);
    max-width: 600px;
    margin: 0 auto 36px;
    line-height: 1.7;
  }
  .hero .hero-cta {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 16px 36px;
    background: var(--accent);
    color: #0a0a0f;
    font-size: 16px;
    font-weight: 700;
    border: none;
    border-radius: 12px;
    cursor: pointer;
    text-decoration: none;
    transition: all 0.2s;
  }
  .hero .hero-cta:hover {
    background: var(--accent-hover);
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(255,140,66,0.3);
  }
  .hero-stats {
    display: flex;
    justify-content: center;
    gap: 48px;
    margin-top: 48px;
  }
  .hero-stats .stat { text-align: center; }
  .hero-stats .stat .num {
    font-size: 28px;
    font-weight: 800;
    color: #fff;
  }
  .hero-stats .stat .label {
    font-size: 13px;
    color: var(--text-muted);
    margin-top: 4px;
  }

  /* ── Section styles ── */
  section {
    padding: 80px 24px;
    max-width: 1200px;
    margin: 0 auto;
  }
  .section-header {
    text-align: center;
    margin-bottom: 48px;
  }
  .section-header h2 {
    font-size: clamp(28px, 4vw, 40px);
    font-weight: 700;
    color: #fff;
    margin-bottom: 12px;
  }
  .section-header p {
    font-size: 16px;
    color: var(--text-muted);
    max-width: 600px;
    margin: 0 auto;
  }
  .section-header .tag {
    display: inline-block;
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 8px;
  }

  /* ── Services ── */
  .services-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 20px;
  }
  .service-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 32px 28px;
    transition: all 0.25s;
    position: relative;
    overflow: hidden;
  }
  .service-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: var(--accent);
    opacity: 0;
    transition: opacity 0.25s;
  }
  .service-card:hover {
    border-color: rgba(255,140,66,0.3);
    transform: translateY(-3px);
  }
  .service-card:hover::before { opacity: 1; }
  .service-card .icon {
    font-size: 32px;
    margin-bottom: 16px;
  }
  .service-card h3 {
    font-size: 18px;
    font-weight: 600;
    color: #fff;
    margin-bottom: 8px;
  }
  .service-card p {
    font-size: 14px;
    color: var(--text-muted);
    line-height: 1.6;
  }
  .service-card .tag-247 {
    display: inline-block;
    margin-top: 12px;
    padding: 4px 12px;
    border-radius: 6px;
    background: rgba(34,197,94,0.12);
    color: var(--green);
    font-size: 12px;
    font-weight: 600;
  }

  /* ── Why Choose Us ── */
  .why-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 24px;
  }
  .why-item {
    text-align: center;
    padding: 28px 20px;
  }
  .why-item .icon {
    font-size: 40px;
    margin-bottom: 12px;
  }
  .why-item h4 {
    font-size: 16px;
    font-weight: 600;
    color: #fff;
    margin-bottom: 6px;
  }
  .why-item p {
    font-size: 14px;
    color: var(--text-muted);
  }

  /* ── Trust / Google Section ── */
  .trust-section {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 40px 32px;
    text-align: center;
  }
  .trust-stars {
    font-size: 32px;
    margin-bottom: 16px;
    letter-spacing: 4px;
  }
  .trust-title {
    font-size: 22px;
    font-weight: 700;
    color: #fff;
    margin-bottom: 8px;
  }
  .trust-text {
    font-size: 15px;
    color: var(--text-muted);
    max-width: 500px;
    margin: 0 auto 20px;
  }
  .trust-badges {
    display: flex;
    justify-content: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .trust-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border-radius: 8px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    font-size: 13px;
    font-weight: 500;
    color: var(--text-muted);
  }
  .trust-badge.highlight { color: var(--accent); }

  /* ── Form Section ── */
  .form-section {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 48px 40px;
    max-width: 640px;
    margin: 0 auto;
  }
  .form-section h3 {
    font-size: 24px;
    font-weight: 700;
    color: #fff;
    margin-bottom: 8px;
    text-align: center;
  }
  .form-section .form-sub {
    text-align: center;
    font-size: 14px;
    color: var(--text-muted);
    margin-bottom: 28px;
  }
  .form-group { margin-bottom: 18px; }
  .form-group label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-muted);
    margin-bottom: 6px;
  }
  .form-group input,
  .form-group select,
  .form-group textarea {
    width: 100%;
    padding: 12px 14px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: #fff;
    font-size: 15px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.2s;
  }
  .form-group input:focus,
  .form-group select:focus,
  .form-group textarea:focus {
    border-color: var(--accent);
  }
  .form-group input::placeholder,
  .form-group textarea::placeholder { color: #555; }
  .form-group select option { background: var(--surface-2); }
  .form-group textarea { min-height: 100px; resize: vertical; }
  .form-group .required::after {
    content: ' *';
    color: #ff4d6a;
  }
  .form-group .hint {
    font-size: 12px;
    color: #555;
    margin-top: 4px;
  }
  .form-submit {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 10px;
    background: var(--accent);
    color: #0a0a0f;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s;
  }
  .form-submit:hover {
    background: var(--accent-hover);
    transform: translateY(-1px);
  }
  .form-submit:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
  }
  .form-result {
    display: none;
    padding: 14px 18px;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 500;
    margin-top: 16px;
    text-align: center;
  }
  .form-result.success {
    display: block;
    background: rgba(34,197,94,0.12);
    border: 1px solid rgba(34,197,94,0.3);
    color: var(--green);
  }
  .form-result.error {
    display: block;
    background: rgba(239,68,68,0.12);
    border: 1px solid rgba(255,77,106,0.3);
    color: #ff4d6a;
  }

  /* ── Contact Section ── */
  .contact-section {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 40px 32px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 32px;
  }
  .contact-info h3 {
    font-size: 20px;
    font-weight: 700;
    color: #fff;
    margin-bottom: 20px;
  }
  .contact-info .contact-item {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
  }
  .contact-info .contact-item .ci-icon {
    width: 40px;
    height: 40px;
    min-width: 40px;
    background: var(--accent-dim);
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 18px;
    color: var(--accent);
  }
  .contact-info .contact-item .ci-label {
    font-size: 12px;
    color: var(--text-muted);
    margin-bottom: 2px;
  }
  .contact-info .contact-item .ci-value {
    font-size: 15px;
    font-weight: 500;
    color: #fff;
  }
  .contact-info .contact-item .ci-value a {
    color: var(--accent);
    text-decoration: none;
  }
  .contact-info .contact-item .ci-value a:hover { text-decoration: underline; }
  .contact-map {
    background: var(--surface-2);
    border-radius: 12px;
    height: 240px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border);
    font-size: 14px;
    color: var(--text-muted);
    flex-direction: column;
    gap: 8px;
  }
  .contact-map .map-icon { font-size: 36px; }
  .contact-map .map-label { font-size: 13px; color: var(--text-muted); }

  /* ── Footer ── */
  footer {
    text-align: center;
    padding: 40px 24px;
    border-top: 1px solid var(--border);
    margin-top: 40px;
  }
  footer p {
    font-size: 13px;
    color: var(--text-muted);
    line-height: 1.8;
  }
  footer .footer-brand {
    font-weight: 700;
    color: var(--accent);
    font-size: 16px;
    margin-bottom: 6px;
  }
  footer .footer-powered {
    font-size: 12px;
    color: #555;
    margin-top: 12px;
  }

  /* ── Responsive ── */
  @media (max-width: 768px) {
    nav .nav-links { display: none; }
    nav .nav-cta { display: none; }
    .mobile-toggle { display: block; }
    nav.mobile-open .nav-links {
      display: flex;
      flex-direction: column;
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      background: rgba(10,10,15,0.98);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      padding: 20px 24px;
      gap: 16px;
      border-bottom: 1px solid var(--border);
    }
    nav.mobile-open .nav-cta {
      display: inline-block;
      margin-top: 8px;
      text-align: center;
    }

    .hero { padding: 100px 20px 60px; }
    .hero-stats { flex-direction: column; gap: 24px; }

    section { padding: 60px 20px; }

    .services-grid { grid-template-columns: 1fr; }
    .why-grid { grid-template-columns: 1fr 1fr; }

    .form-section { padding: 32px 24px; }

    .contact-section {
      grid-template-columns: 1fr;
      padding: 28px 24px;
    }
  }
  @media (max-width: 480px) {
    .why-grid { grid-template-columns: 1fr; }
    .trust-badges { flex-direction: column; align-items: center; }
    .hero h1 { font-size: 30px; }
  }
</style>
</head>
<body>

<!-- Navigation -->
<nav id="navbar">
  <div class="brand">
    <div class="brand-icon">🔧</div>
    <div class="brand-text">Bob's <span>Plumbing</span></div>
  </div>
  <ul class="nav-links">
    <li><a href="#services">Services</a></li>
    <li><a href="#why-us">Why Us</a></li>
    <li><a href="#quote">Free Quote</a></li>
    <li><a href="#contact">Contact</a></li>
  </ul>
  <a href="#quote" class="nav-cta">📞 Get a Quote</a>
  <button class="mobile-toggle" onclick="toggleMobile()" aria-label="Toggle menu">☰</button>
</nav>

<!-- Hero -->
<section class="hero">
  <div class="hero-content">
    <div class="hero-badge">✅ Licensed &bull; Insured &bull; 15+ Years Experience</div>
    <h1>Sydney's Trusted<br><span>Plumbing Service</span></h1>
    <p>Fast, reliable, and professional plumbing services across Sydney. Available 24/7 for emergencies. Your satisfaction is guaranteed.</p>
    <a href="#quote" class="hero-cta">
      Get a Free Quote
      <span>→</span>
    </a>
    <div class="hero-stats">
      <div class="stat">
        <div class="num">5,000+</div>
        <div class="label">Jobs Completed</div>
      </div>
      <div class="stat">
        <div class="num">⭐ 4.9</div>
        <div class="label">Google Rating</div>
      </div>
      <div class="stat">
        <div class="num">15+</div>
        <div class="label">Years Experience</div>
      </div>
    </div>
  </div>
</section>

<!-- Services -->
<section id="services">
  <div class="section-header">
    <div class="tag">What We Do</div>
    <h2>Our Plumbing Services</h2>
    <p>From emergency repairs to complete installations, we handle all your plumbing needs.</p>
  </div>
  <div class="services-grid">
    <div class="service-card">
      <div class="icon">⚡</div>
      <h3>Emergency Repairs</h3>
      <p>24/7 emergency plumbing services across Sydney. Burst pipes, severe leaks, and urgent blockages — we're there when you need us most.</p>
      <span class="tag-247">🔴 24/7 Emergency</span>
    </div>
    <div class="service-card">
      <div class="icon">🔥</div>
      <h3>Hot Water Systems</h3>
      <p>Installation, repair, and replacement of all hot water systems — gas, electric, solar, and heat pump. Same-day service available.</p>
    </div>
    <div class="service-card">
      <div class="icon">🚫</div>
      <h3>Blocked Drains</h3>
      <p>Using advanced CCTV drain cameras and high-pressure jetting to clear even the toughest blockages fast and effectively.</p>
    </div>
    <div class="service-card">
      <div class="icon">🔥</div>
      <h3>Gas Fitting</h3>
      <p>Licensed gas fitters for all gas appliance installations, repairs, and safety inspections. Gas cooktops, heaters, BBQs and more.</p>
    </div>
    <div class="service-card">
      <div class="icon">🔍</div>
      <h3>Leak Detection</h3>
      <p>Non-invasive electronic leak detection technology to find hidden water leaks without breaking walls or floors. Fast and precise.</p>
    </div>
  </div>
</section>

<!-- Why Us -->
<section id="why-us" style="padding-top:0;">
  <div class="section-header">
    <div class="tag">Why Choose Bob's</div>
    <h2>Why Sydney Trusts Us</h2>
    <p>We don't just fix pipes — we build relationships with quality workmanship.</p>
  </div>
  <div class="why-grid">
    <div class="why-item">
      <div class="icon">🏆</div>
      <h4>Licensed &amp; Insured</h4>
      <p>Fully licensed, insured plumbers giving you complete peace of mind.</p>
    </div>
    <div class="why-item">
      <div class="icon">⏱️</div>
      <h4>Same-Day Service</h4>
      <p>Most jobs attended within hours. We respect your time.</p>
    </div>
    <div class="why-item">
      <div class="icon">💰</div>
      <h4>Upfront Pricing</h4>
      <p>No hidden fees. You'll know the cost before any work begins.</p>
    </div>
    <div class="why-item">
      <div class="icon">💯</div>
      <h4>Satisfaction Guaranteed</h4>
      <p>If you're not happy, we'll make it right. No questions asked.</p>
    </div>
  </div>
</section>

<!-- Trust / Google -->
<section style="padding-top:0;padding-bottom:40px;">
  <div class="trust-section">
    <div class="trust-stars">⭐⭐⭐⭐⭐</div>
    <div class="trust-title">Rated 4.9/5 on Google</div>
    <div class="trust-text">"Bob's Plumbing saved our Christmas dinner! Burst pipe on Christmas Eve and they were here in 30 minutes. Absolute legends."</div>
    <div class="trust-badges">
      <span class="trust-badge">📋 500+ 5-Star Reviews</span>
      <span class="trust-badge highlight">🏅 Google Guaranteed</span>
      <span class="trust-badge">⭐ Top Rated 2024</span>
    </div>
  </div>
</section>

<!-- Quote Form -->
<section id="quote" style="padding-top:0;">
  <div class="form-section">
    <h3>Get a Free Quote</h3>
    <div class="form-sub">Fill in the form below and we'll get back to you within 1 hour.</div>
    <form id="quote-form" onsubmit="return submitQuote(event)">
      <div class="form-group">
        <label class="required">Full Name</label>
        <input type="text" id="form-name" placeholder="e.g. John Smith" required>
      </div>
      <div class="form-group">
        <label class="required">Phone Number</label>
        <input type="tel" id="form-phone" placeholder="e.g. 0400 123 456" required>
        <div class="hint">We'll call you to schedule a free estimate</div>
      </div>
      <div class="form-group">
        <label class="required">Email Address</label>
        <input type="email" id="form-email" placeholder="e.g. john@example.com" required>
      </div>
      <div class="form-group">
        <label class="required">Service Needed</label>
        <select id="form-service" required>
          <option value="">Select a service...</option>
          <option value="Emergency Repairs">Emergency Repairs</option>
          <option value="Hot Water System">Hot Water System</option>
          <option value="Blocked Drains">Blocked Drains</option>
          <option value="Gas Fitting">Gas Fitting</option>
          <option value="Leak Detection">Leak Detection</option>
          <option value="General Plumbing">General Plumbing</option>
          <option value="Other">Other</option>
        </select>
      </div>
      <div class="form-group">
        <label>Describe the Issue</label>
        <textarea id="form-message" placeholder="Tell us about your plumbing issue... e.g. Kitchen sink is blocked, hot water not working"></textarea>
      </div>
      <button type="submit" class="form-submit" id="form-submit-btn">📋 Get Free Quote</button>
      <div class="form-result" id="form-result"></div>
    </form>
  </div>
</section>

<!-- Contact -->
<section id="contact" style="padding-top:0;">
  <div class="section-header">
    <div class="tag">Get in Touch</div>
    <h2>Contact Us</h2>
    <p>We're here to help. Reach out anytime — day or night.</p>
  </div>
  <div class="contact-section" style="max-width:860px;margin:0 auto;">
    <div class="contact-info">
      <h3>Bob's Plumbing Services</h3>
      <div class="contact-item">
        <div class="ci-icon">📞</div>
        <div>
          <div class="ci-label">Phone (24/7)</div>
          <div class="ci-value"><a href="tel:0400123456">0400 123 456</a></div>
        </div>
      </div>
      <div class="contact-item">
        <div class="ci-icon">✉️</div>
        <div>
          <div class="ci-label">Email</div>
          <div class="ci-value"><a href="mailto:emilio.pegolo1@gmail.com">emilio.pegolo1@gmail.com</a></div>
        </div>
      </div>
      <div class="contact-item">
        <div class="ci-icon">📍</div>
        <div>
          <div class="ci-label">Service Area</div>
          <div class="ci-value">Sydney Metro &bull; Eastern Suburbs &bull; Inner West</div>
        </div>
      </div>
      <div class="contact-item">
        <div class="ci-icon">⏰</div>
        <div>
          <div class="ci-label">Hours</div>
          <div class="ci-value">24/7 — Always open for emergencies</div>
        </div>
      </div>
    </div>
    <div class="contact-map">
      <div class="map-icon">🗺️</div>
      <div class="map-label">Serving all of Sydney Metropolitan Area</div>
      <div style="font-size:12px;color:#555;">Emergency response within 60 minutes</div>
    </div>
  </div>
</section>

<!-- Footer -->
<footer>
  <div class="footer-brand">🔧 Bob's Plumbing Services</div>
  <p>Licensed &bull; Insured &bull; Trusted Since 2010<br>Sydney's go-to for professional plumbing</p>
  <div class="footer-powered">Powered by <a href="/" style="color:var(--accent);text-decoration:none;font-weight:600;">Automate Pro</a></div>
</footer>

<script>
function toggleMobile() {
  document.getElementById('navbar').classList.toggle('mobile-open');
}

// Close mobile menu on link click
document.querySelectorAll('.nav-links a').forEach(a => {
  a.addEventListener('click', () => {
    document.getElementById('navbar').classList.remove('mobile-open');
  });
});

async function submitQuote(e) {
  e.preventDefault();
  const btn = document.getElementById('form-submit-btn');
  const result = document.getElementById('form-result');

  const name = document.getElementById('form-name').value.trim();
  const phone = document.getElementById('form-phone').value.trim();
  const email = document.getElementById('form-email').value.trim();
  const service = document.getElementById('form-service').value;
  const message = document.getElementById('form-message').value.trim();

  if (!name || !phone || !email || !service) {
    result.textContent = 'Please fill in all required fields.';
    result.className = 'form-result error';
    return false;
  }

  btn.disabled = true;
  btn.textContent = 'Sending...';
  result.className = 'form-result';
  result.textContent = '';

  try {
      const res = await fetch('/api/lead', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: name,
          phone: phone,
          email: email,
          business_type: 'Trades & Construction',
          message: 'Service: ' + service + (message ? ' \u2014 ' + message : ''),
          notify_email: 'bob@bobsplumbing.com'
        })
      });
    const data = await res.json();
    if (data.success) {
      result.textContent = '✅ Thanks ' + name + '! We\'ll call you within 1 hour to schedule your free estimate.';
      result.className = 'form-result success';
      document.getElementById('quote-form').reset();
      // Scroll to show the success message
      result.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      result.textContent = '❌ ' + (data.error || 'Something went wrong. Please try again.');
      result.className = 'form-result error';
    }
  } catch (err) {
    result.textContent = '❌ Connection error. Please try again.';
    result.className = 'form-result error';
  }

  btn.disabled = false;
  btn.textContent = '📋 Get Free Quote';
  return false;
}
</script>

</body>
</html>"""


@app.route("/demos/plumber")
def plumber_demo():
    return render_template_string(PLUMBER_HTML)


# Run DB init at import time (gunicorn doesn't run __main__)
try:
    init_db()
    print("[INIT] Database tables ready")
except Exception as e:
    print(f"[INIT] Database init failed (will retry on first request): {e}")

# ── React SPA catch-all (must be LAST route) ──────────────────────────────

@app.route("/<path:fallback_path>")
def serve_react_fallback(fallback_path):
    if fallback_path.startswith("api/") or fallback_path.startswith("portal/") or \
       fallback_path.startswith("checkout/") or fallback_path.startswith("demos/"):
        return "Not found", 404
    return serve_react("index.html")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  Automate Pro — Full Business System")
    print("=" * 50)
    print(f"  DB:       {DB_PATH}")
    print(f"  Notify:   {NOTIFY_EMAIL}")
    print(f"  Running:  http://localhost:5002")
    print("=" * 50)
    print("  Creating Stripe test products...")
    create_test_products()
    print("=" * 50)
    print("  Endpoints:")
    print("    POST /api/lead             — Capture a new lead")
    print("    GET  /api/leads            — List all leads")
    print("    GET  /api/config           — Get Stripe public key")
    print("    POST /api/create-checkout-session — Create Stripe checkout")
    print("    POST /api/stripe-webhook   — Stripe webhook (test mode)")
    print("    POST /api/calendly-webhook — Calendly webhook (auto-update lead status)")
    print("    GET  /api/dashboard        — Dashboard stats (auth)")
    print("    PUT  /api/lead/<id>        — Update lead (auth)")
    print("    GET  /api/lead/<id>        — Get lead (auth)")
    print("    POST /api/lead/<id>/send-email — Email lead (auth)")
    print("    GET  /api/invoices         — List invoices (auth)")
    print("    POST /api/invoices         — Create invoice (auth)")
    print("    GET  /api/invoices/<id>    — Get invoice (auth)")
    print("    PUT  /api/invoices/<id>    — Update invoice (auth)")
    print("    POST /api/invoices/<id>/send — Send invoice email (auth)")
    print("    GET  /invoice/<id>         — Public invoice view")
    print("    GET  /login                — Login page")
    print("    POST /login                — Login action")
    print("    POST /logout               — Logout")
    print("    GET  /dashboard            — Admin dashboard (auth)")
    print("    GET  /checkout/success     — Thank-you page")
    print("    GET  /demos/plumber         — Plumber demo page")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5002)), debug=False)
