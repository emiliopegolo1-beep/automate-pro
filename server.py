#!/usr/bin/env python3
"""Automate Pro — Lead Capture & Admin Dashboard Server."""
import os
import sys
import sqlite3
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

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db")
NOTIFY_EMAIL = "emilio.pegolo1@gmail.com"
DASHBOARD_PASSWORD = "automate2026"

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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.execute(
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    # Safely add columns that may not exist on older databases
    columns_to_add = [
        ("phone", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'new'"),
        ("notes", "TEXT DEFAULT ''"),
        ("revenue", "REAL DEFAULT 0"),
        ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ("requirements", "TEXT DEFAULT ''"),
        ("quoted_price", "REAL DEFAULT 0"),
        ("follow_up_date", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT 'website'"),
    ]
    for col_name, col_def in columns_to_add:
        try:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # Payments table
    conn.execute(
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Invoices table
    conn.execute(
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at TIMESTAMP
        )
    """
    )

    conn.commit()
    conn.close()


def save_lead(lead_id, name, email, business_type, message):
    conn = get_db()
    conn.execute(
        "INSERT INTO leads (id, name, email, business_type, message) VALUES (?, ?, ?, ?, ?)",
        (lead_id, name, email, business_type, message),
    )
    conn.commit()
    conn.close()


def get_lead_by_id(lead_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_lead_in_db(lead_id, updates):
    """Update lead columns. `updates` is a dict of column → value."""
    allowed = {"name", "email", "business_type", "phone", "status", "notes", "revenue", "requirements", "quoted_price", "follow_up_date", "source"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return False
    filtered["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    values = list(filtered.values()) + [lead_id]
    conn = get_db()
    conn.execute(f"UPDATE leads SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def mark_auto_responded(lead_id):
    conn = get_db()
    conn.execute("UPDATE leads SET auto_responded = 1 WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()


def mark_notified(lead_id):
    conn = get_db()
    conn.execute("UPDATE leads SET notified = 1 WHERE id = ?", (lead_id,))
    conn.commit()
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
    row = conn.execute("SELECT invoice_number FROM invoices ORDER BY invoice_number DESC LIMIT 1").fetchone()
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
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def build_invoice_email_body(inv):
    return (
        f"Hi {inv['client_name']},\n\n"
        f"Your invoice #{inv['invoice_number']} for ${inv['amount']:.2f} is ready.\n\n"
        f"View invoice: https://automatepro.ai/invoice/{inv['id']}\n"
        f"Due: {inv.get('due_date') or 'Upon receipt'}\n\n"
        "Thanks,\n"
        "Emilio\n"
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



@app.route("/")
def serve_index():
    with open(os.path.join(os.path.dirname(__file__), "index.html"), "r") as f:
        return f.read()

@app.route("/api/lead", methods=["POST"])
def api_lead():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    business_type = (data.get("business_type") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email:
        return jsonify({"error": "Name and email are required"}), 400

    lead_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    save_lead(lead_id, name, email, business_type, message)

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
    result = send_email(NOTIFY_EMAIL, notify_subject, notify_body)
    if result.get("success"):
        mark_notified(lead_id)
    else:
        gmail_errors.append(f"Notify failed: {result.get('error')}")

    response = {"success": True, "lead_id": lead_id, "name": name}
    if gmail_errors:
        response["gmail_warnings"] = gmail_errors

    return jsonify(response), 201


@app.route("/api/leads", methods=["GET"])
def api_leads():
    conn = get_db()
    rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── Dashboard API Endpoints ──────────────────────────────────────────────────


@app.route("/api/dashboard", methods=["GET"])
@login_required
def api_dashboard():
    conn = get_db()

    # Total leads
    total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]

    # Leads by status
    status_rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM leads GROUP BY status"
    ).fetchall()
    leads_by_status = {r["status"]: r["cnt"] for r in status_rows}
    for s in ("new", "call_scheduled", "call_done", "building", "demo_ready", "delivered", "paid"):
        leads_by_status.setdefault(s, 0)

    # Revenue totals (from payments table)
    total_revenue = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'completed'"
    ).fetchone()[0]

    # Revenue this month
    first_of_month = date.today().replace(day=1).isoformat()
    revenue_this_month = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'completed' AND created_at >= ?",
        (first_of_month,),
    ).fetchone()[0]

    # New this week
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    new_this_week = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE created_at >= ?", (week_ago,)
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
    recent = conn.execute(
        "SELECT * FROM leads ORDER BY created_at DESC LIMIT 5"
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
    rows = conn.execute("SELECT * FROM invoices ORDER BY created_at DESC").fetchall()
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

    invoice_id = str(uuid.uuid4())[:8]
    invoice_number = generate_invoice_number()

    conn = get_db()
    conn.execute(
        """INSERT INTO invoices
           (id, lead_id, client_name, client_email, amount, description, due_date, invoice_number)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_id, lead_id, client_name, client_email, amount, description, due_date, invoice_number),
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

    allowed_updates = {"status", "paid_at", "description", "due_date", "amount"}
    updates = {k: v for k, v in data.items() if k in allowed_updates}

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    # If marking as paid, set paid_at
    if updates.get("status") == "paid" and not inv.get("paid_at"):
        updates["paid_at"] = datetime.now().isoformat()

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [invoice_id]

    conn = get_db()
    conn.execute(f"UPDATE invoices SET {set_clause} WHERE id = ?", values)
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

    subject = f"Invoice #{inv['invoice_number']} from Automate Pro"
    body = build_invoice_email_body(inv)

    result = send_email(inv["client_email"], subject, body)
    if result.get("success"):
        # Update status to sent if it was draft
        if inv["status"] == "draft":
            conn = get_db()
            conn.execute("UPDATE invoices SET status = ? WHERE id = ?", ("sent", invoice_id))
            conn.commit()
            conn.close()
        return jsonify({"success": True, "message": f"Invoice sent to {inv['client_email']}"})
    else:
        return jsonify({"error": result.get("error", "Failed to send email")}), 500


@app.route("/invoice/<invoice_id>")
@app.route("/inv/<inv_num>")
def public_invoice_view(invoice_id):
    """Public invoice view — no auth required, beautiful printable page."""
    inv = get_invoice_by_id(invoice_id)
    if not inv:
        return render_template_string(INVOICE_NOT_FOUND_HTML)
    return render_template_string(INVOICE_PAGE_HTML, inv=inv)


# ── Stripe Endpoints ────────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def api_config():
    return jsonify({"publishableKey": STRIPE_PUBLISHABLE_KEY})


@app.route("/api/create-checkout-session", methods=["POST"])
def api_create_checkout_session():
    data = request.get_json() or {}
    
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
    else:
        print(f"  [Webhook] Unhandled event type: {event_type}")

    return jsonify({"received": True}), 200


def handle_checkout_completed(session_data):
    """Process a successful checkout completion."""
    session_id = session_data.get("id")
    metadata = session_data.get("metadata", {}) or {}
    plan_key = metadata.get("plan_key", "unknown")
    lead_id = metadata.get("lead_id")
    customer_email = session_data.get("customer_details", {}).get("email", "") or ""

    # Calculate amount from the session
    amount_total = session_data.get("amount_total", 0) / 100.0
    currency = session_data.get("currency", "usd") or "usd"

    # Determine plan display name and payment type
    plan = PLANS.get(plan_key, {})
    plan_name = plan.get("name", plan_key)
    payment_type = "setup" if plan.get("type") == "one_time" else "subscription"

    payment_id = str(uuid.uuid4())[:8]
    conn = get_db()
    conn.execute(
        """INSERT INTO payments
           (id, lead_id, stripe_session_id, amount, currency, plan_name, payment_type, status, customer_email)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        conn.execute(
            "UPDATE leads SET status = 'paid', revenue = COALESCE(revenue, 0) + ? WHERE id = ?",
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


@app.route("/checkout/success")
def checkout_success():
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
    background: rgba(0,212,170,0.1);
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
  .status-badge.paid { background: rgba(0,212,170,0.15); color: var(--green); }
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
              {{inv['description'] or 'Professional Automation Service'}}
              <div class="desc">{{inv['invoice_number']}}</div>
            </td>
            <td>${{'{:,.2f}'.format(inv['amount'])}}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Total -->
    <div class="invoice-total">
      <div class="total-label">Total Due</div>
      <div class="total-amount">${{'{:,.2f}'.format(inv['amount'])}}</div>
      <div style="margin-top:8px;">
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
</body>
</html>"""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Automate Pro — Login</title>
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
  }
  .login-card {
    background: #12121a;
    border: 1px solid #2a2a3a;
    border-radius: 16px;
    padding: 48px 40px;
    width: 100%;
    max-width: 400px;
    text-align: center;
  }
  .login-card h1 { font-size: 24px; margin-bottom: 4px; color: #fff; }
  .login-card .subtitle { color: #888; font-size: 14px; margin-bottom: 32px; }
  .login-card .brand { color: #ff8c42; font-weight: 700; font-size: 28px; margin-bottom: 8px; }
  .login-card input {
    width: 100%;
    padding: 14px 16px;
    border-radius: 10px;
    border: 1px solid #2a2a3a;
    background: #1a1a26;
    color: #fff;
    font-size: 16px;
    outline: none;
    transition: border-color 0.2s;
    margin-bottom: 16px;
  }
  .login-card input:focus { border-color: #ff8c42; }
  .login-card input::placeholder { color: #666; }
  .login-card button {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 10px;
    background: #ff8c42;
    color: #fff;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  .login-card button:hover { background: #e07a30; }
  .login-card .error { color: #ff4d4d; font-size: 14px; margin-top: 12px; display: none; }
</style>
</head>
<body>
<div class="login-card">
  <div class="brand">Automate Pro</div>
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
    --green-dim: #009977;
    --red: #ff4d6a;
    --pink: #ff6b9d;
    --blue: #4dabf7;
    --yellow: #ffd43b;
  }
  html, body { height: 100%; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    display: flex;
  }

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
    gap: 12px;
    padding: 12px 20px;
    color: var(--text-muted);
    text-decoration: none;
    font-size: 14px;
    font-weight: 500;
    transition: all 0.15s;
    border-left: 3px solid transparent;
  }
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
  }
  .sidebar .logout-btn:hover {
    color: var(--red);
    border-color: var(--red);
    background: rgba(255,77,106,0.08);
  }

  /* ── Main content ── */
  .main {
    flex: 1;
    padding: 32px;
    overflow-y: auto;
    min-width: 0;
  }
  .main h2 { font-size: 28px; font-weight: 700; margin-bottom: 24px; color: #fff; }
  .section-title {
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 16px;
    margin-top: 36px;
    color: #fff;
  }
  .section-title:first-of-type { margin-top: 0; }

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
  .stat-card .value { font-size: 32px; font-weight: 700; color: #fff; }
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
    background: rgba(255,140,66,0.1);
    padding: 2px 8px;
    border-radius: 4px;
    margin-top: 4px;
  }
  .kanban-col.empty .col-header { opacity: 0.5; }
  .kanban-col.empty .empty-msg {
    color: var(--text-muted);
    font-size: 13px;
    text-align: center;
    padding: 24px 0;
    opacity: 0.4;
  }

  /* Colored dots for columns */
  /* Pipeline column colors */
  .kanban-col[data-status="new"] { border-top: 3px solid var(--blue); }
  .kanban-col[data-status="call_scheduled"] { border-top: 3px solid var(--yellow); }
  .kanban-col[data-status="call_done"] { border-top: 3px solid var(--accent); }
  .kanban-col[data-status="building"] { border-top: 3px solid var(--pink); }
  .kanban-col[data-status="demo_ready"] { border-top: 3px solid var(--green-dim); }
  .kanban-col[data-status="delivered"] { border-top: 3px solid var(--green); }
  .kanban-col[data-status="paid"] { border-top: 3px solid #00e6b8; }

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
    font-size: 24px;
    cursor: pointer;
    padding: 4px 8px;
    border-radius: 6px;
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
  .badge-call_done { background: rgba(255,140,66,0.15); color: var(--accent); }
  .badge-building { background: rgba(255,107,157,0.15); color: var(--pink); }
  .badge-demo_ready { background: rgba(0,153,119,0.2); color: var(--green-dim); }
  .badge-delivered { background: rgba(0,212,170,0.15); color: var(--green); }
  .badge-paid { background: rgba(0,230,184,0.15); color: #00e6b8; }

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
  }
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
  .email-result.success { display: block !important; background: rgba(0,212,170,0.1); color: var(--green); }
  .email-result.error { display: block !important; background: rgba(255,77,106,0.1); color: var(--red); }

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
  .revenue-card .amount { font-size: 28px; font-weight: 700; color: var(--green); }

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
  }
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
  }
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
  .inv-badge.paid { background: rgba(0,212,170,0.15); color: var(--green); }
  .inv-badge.overdue { background: rgba(255,77,106,0.15); color: var(--red); }
  .inv-badge.cancelled { background: rgba(136,136,136,0.15); color: var(--text-muted); }

  .toast.show { display: block; }
  .toast.success { background: rgba(0,212,170,0.15); border: 1px solid var(--green); color: var(--green); }
  .toast.error { background: rgba(255,77,106,0.15); border: 1px solid var(--red); color: var(--red); }
  @keyframes slideIn { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
</style>
</head>
<body>

<!-- Sidebar -->
<div class="sidebar">
  <div class="brand">Automate Pro</div>
  <nav>
    <a href="#" class="active" onclick="switchTab('dashboard'); return false;">📊 Dashboard</a>
    <a href="#" onclick="switchTab('invoices'); return false;">📄 Invoices</a>
    <a href="#" onclick="switchTab('revenue'); return false;">💰 Revenue</a>
    <a href="#" onclick="switchTab('pipeline'); return false;">📋 Pipeline</a>
  </nav>
  <div class="logout-section">
    <button class="logout-btn" onclick="logout()">🚪 Sign Out</button>
  </div>
</div>

<!-- Main content -->
<div class="main" id="main-content">
  <h2>📊 Dashboard</h2>
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
      <h2 style="margin-bottom:0;">📄 Invoices</h2>
      <button class="btn btn-accent" onclick="openCreateInvoiceModal()">+ Create Invoice</button>
    </div>
    <div class="table-card">
      <table class="data-table" id="invoices-table">
        <thead>
          <tr>
            <th>Invoice #</th>
            <th>Client</th>
            <th>Amount</th>
            <th>Status</th>
            <th>Date</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="invoices-body">
          <tr><td colspan="6" class="loading">Loading invoices</td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div>

<!-- Create Invoice Modal -->
<div class="modal-overlay" id="create-invoice-modal">
  <div class="modal">
    <button class="close-btn" onclick="closeCreateInvoiceModal()">&times;</button>
    <h3>Create Invoice</h3>
    <div class="modal-sub">Generate a new invoice for a client</div>
    <div class="form-group">
      <label>Client Name</label>
      <input type="text" id="inv-client-name" placeholder="Full name">
    </div>
    <div class="form-group">
      <label>Client Email</label>
      <input type="email" id="inv-client-email" placeholder="client@example.com">
    </div>
    <div class="form-group">
      <label>Amount ($)</label>
      <input type="number" id="inv-amount" placeholder="0.00" step="0.01" min="0">
    </div>
    <div class="form-group">
      <label>Description</label>
      <input type="text" id="inv-description" placeholder="Service description (e.g. Automation Setup)">
    </div>
    <div class="form-group">
      <label>Due Date (optional)</label>
      <input type="date" id="inv-due-date">
    </div>
    <div class="form-group" id="inv-lead-group" style="display:none;">
      <label>Lead ID</label>
      <input type="text" id="inv-lead-id" readonly style="opacity:0.6;font-size:12px;">
    </div>
    <div class="btn-row" style="margin-bottom:0;">
      <button class="btn btn-accent" onclick="createInvoice()">Generate Invoice</button>
      <button class="btn btn-outline" onclick="closeCreateInvoiceModal()">Cancel</button>
    </div>
    <div class="email-result" id="create-invoice-result"></div>
  </div>
</div>

<!-- Confirm Invoice Modal -->
<div class="modal-overlay" id="confirm-invoice-modal">
  <div class="modal" style="max-width:420px;">
    <button class="close-btn" onclick="document.getElementById('confirm-invoice-modal').classList.remove('open')">&times;</button>
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
    <button class="close-btn" onclick="closeModal()">&times;</button>
    <h3 id="modal-name">—</h3>
    <div class="modal-sub" id="modal-email">—</div>

    <!-- Section 1: Lead Info (Read-only) -->
    <div style="margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--border);">
      <div style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;">📋 Lead Info</div>
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
      <div style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;">⚙️ Workflow</div>
      <div class="form-group">
        <label>Status</label>
        <select id="modal-status-select" onchange="updateLeadStatus()">
          <option value="new">🆕 New</option>
          <option value="call_scheduled">📅 Call Scheduled</option>
          <option value="call_done">✅ Call Done</option>
          <option value="building">🔧 Building</option>
          <option value="demo_ready">🎬 Demo Ready</option>
          <option value="delivered">📦 Delivered</option>
          <option value="paid">💰 Paid</option>
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
      <div style="font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:12px;">🚀 Quick Actions</div>
      <div class="btn-row">
        <button class="btn btn-build btn-sm" onclick="buildNow()" style="background:#00d4aa;color:#0a0a0f;border:none;padding:8px 14px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer;">🤖 Build Now</button>
        <button class="btn btn-green btn-sm" onclick="createInvoiceForLead()">📄 Create Invoice</button>
        <button class="btn btn-accent btn-sm" onclick="copyCalendlyLink()">🔗 Copy Calendly Link</button>
        <button class="btn btn-outline btn-sm" onclick="copyRequirementsSummary()">📋 Copy Requirements</button>
      </div>
      <div class="email-section">
        <div class="form-group" style="margin-top:12px;">
          <label>Send Email to Lead</label>
          <input type="text" id="email-subject" placeholder="Subject" style="margin-bottom:8px;">
          <textarea id="email-body" placeholder="Email body..." style="min-height:80px;"></textarea>
        </div>
        <button class="btn btn-outline btn-sm" onclick="sendEmailToLead()">📧 Send Email</button>
        <div class="email-result" id="email-result"></div>
      </div>
    </div>
  </div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
let currentLeadId = null;
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
async function loadDashboard() {
  const data = await fetchJSON('/api/dashboard');
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
    toast('✅ Copied! Now tell Jarvis: "Build this automation"', 'success');
  }).catch(() => {
    prompt('Copy this text and send to Jarvis:', text);
  });
}

function copyCalendlyLink() {
  const link = 'https://calendly.com/emilio-pegolo1/30min';
  navigator.clipboard.writeText(link).then(() => {
    toast('📅 Calendly link copied!', 'success');
  }).catch(() => {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = link;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast('📅 Calendly link copied!', 'success');
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
    toast('📋 Requirements summary copied!', 'success');
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = summary;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast('📋 Requirements summary copied!', 'success');
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
    resultDiv.textContent = '✅ ' + res.message;
    resultDiv.className = 'email-result success';
    toast('Email sent!', 'success');
  } else {
    resultDiv.textContent = '❌ ' + (res ? res.error : 'Failed to send');
    resultDiv.className = 'email-result error';
  }
}

// ── Invoice Functions ──

async function loadInvoices() {
  const invs = await fetchJSON('/api/invoices');
  if (!invs) return;
  const tbody = $('invoices-body');
  if (invs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#888;padding:40px;">No invoices yet. Create your first one!</td></tr>';
    return;
  }
  tbody.innerHTML = invs.map(inv => {
    const statusClass = inv.status || 'draft';
    const dateStr = inv.created_at ? inv.created_at.slice(0, 10) : '—';
    const leadAttr = inv.lead_id ? `data-lead="${inv.lead_id}"` : '';
    return `<tr ${leadAttr}>
      <td style="font-weight:600;color:#fff;">${escapeHtml(inv.invoice_number)}</td>
      <td>
        <div style="font-weight:500;">${escapeHtml(inv.client_name)}</div>
        <div style="font-size:12px;color:#888;">${escapeHtml(inv.client_email)}</div>
      </td>
      <td style="font-weight:600;color:#fff;">$${Number(inv.amount).toLocaleString('en-AU', {minimumFractionDigits:2})}</td>
      <td><span class="inv-badge ${statusClass}">${statusClass}</span></td>
      <td style="color:#888;font-size:13px;">${dateStr}</td>
      <td>
        <div class="actions">
          <button class="view-btn" onclick="window.open('/invoice/${inv.id}','_blank')">👁️ View</button>
          ${statusClass !== 'paid' && statusClass !== 'cancelled' ? `<button class="send" onclick="sendInvoice('${inv.id}')">📧 Send</button>` : ''}
          ${statusClass === 'sent' ? `<button class="pay" onclick="markInvoicePaid('${inv.id}')">✅ Paid</button>` : ''}
        </div>
      </td>
    </tr>`;
  }).join('');
}

async function sendInvoice(id) {
  const res = await fetchJSON('/api/invoices/' + id + '/send', { method: 'POST' });
  if (res && res.success) {
    toast('✅ ' + res.message, 'success');
    loadInvoices();
  } else {
    toast('❌ ' + (res ? res.error : 'Failed to send'), 'error');
  }
}

async function markInvoicePaid(id) {
  const res = await fetchJSON('/api/invoices/' + id, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'paid' })
  });
  if (res) {
    toast('✅ Invoice marked as paid', 'success');
    loadInvoices();
  } else {
    toast('❌ Failed to update invoice', 'error');
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
  $('create-invoice-result').className = 'email-result';
  $('create-invoice-result').textContent = '';
  $('create-invoice-modal').classList.add('open');
}

function closeCreateInvoiceModal() {
  $('create-invoice-modal').classList.remove('open');
}

function createInvoiceForLead() {
  if (!currentLeadId) return;
  // Fetch lead details to pre-fill
  fetchJSON('/api/lead/' + currentLeadId).then(lead => {
    if (!lead) return;
    $('inv-client-name').value = lead.name || '';
    $('inv-client-email').value = lead.email || '';
    $('inv-amount').value = lead.quoted_price > 0 ? lead.quoted_price : '';
    $('inv-description').value = (lead.requirements || '') + ' - Automation Service';
    $('inv-due-date').value = lead.follow_up_date || '';
    $('inv-lead-id').value = currentLeadId;
    $('inv-lead-group').style.display = 'block';
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
  const resultDiv = $('create-invoice-result');

  if (!name || !email) {
    resultDiv.textContent = 'Please enter client name and email.';
    resultDiv.className = 'email-result error';
    return;
  }
  if (!amount || amount <= 0) {
    resultDiv.textContent = 'Please enter a valid amount.';
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
    due_date: due
  };
  if (leadId) body.lead_id = leadId;

  const res = await fetchJSON('/api/invoices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  if (res && !res.error) {
    resultDiv.textContent = '✅ Invoice ' + (res.invoice_number || '') + ' created!';
    resultDiv.className = 'email-result success';
    toast('Invoice ' + (res.invoice_number || '') + ' created!', 'success');
    closeCreateInvoiceModal();
    loadInvoices();
  } else {
    resultDiv.textContent = '❌ ' + (res ? res.error : 'Failed to create invoice');
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
    $('main-content').querySelector('h2').textContent = '📊 Dashboard';
    $('stats-grid').style.display = 'grid';
    document.querySelector('.section-title').textContent = 'Pipeline';
    document.querySelector('.section-title').style.display = 'block';
    $('kanban').style.display = 'grid';
    $('revenue-title').style.display = 'none';
    $('revenue-grid').style.display = 'none';
    loadDashboard();
  } else if (tab === 'revenue') {
    $('main-content').querySelector('h2').textContent = '💰 Revenue';
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
    $('main-content').querySelector('h2').textContent = '📋 Pipeline';
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
    """Test SMTP connection and return diagnostics."""
    import traceback
    result = {"smtp_configured": bool(SMTP_USER and SMTP_PASS)}
    if result["smtp_configured"]:
        try:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
            server.starttls()
            server.login(SMTP_USER, "****" + SMTP_PASS[-4:] if len(SMTP_PASS) > 4 else "****")
            server.quit()
            result["login"] = "success"
            # Try sending
            result["send_test"] = send_email(SMTP_USER, "Railway SMTP Test", "If you see this, SMTP works on Railway!")
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
    return jsonify(result)

if __name__ == "__main__":
    init_db()
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
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5002)), debug=False)
