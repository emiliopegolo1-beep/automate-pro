# Automate Pro — Invoice System Build Spec (Updated)

## Overview
Professional invoice system with **both one-time setup fees and recurring subscriptions**. Cancel a subscription → emails Emilio so he can take the client's website down.

## Database — Invoices Table
```sql
CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY,
  lead_id TEXT,
  client_name TEXT NOT NULL,
  client_email TEXT NOT NULL,
  amount REAL NOT NULL,               -- One-time setup fee
  description TEXT DEFAULT '',
  status TEXT DEFAULT 'draft',         -- draft, sent, paid, overdue, cancelled
  due_date TEXT,
  invoice_number TEXT UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  paid_at TIMESTAMP,
  has_subscription INTEGER DEFAULT 0,  -- 1 = has recurring subscription
  sub_amount REAL DEFAULT 0,           -- Recurring amount (e.g. 197.00)
  sub_interval TEXT DEFAULT 'month',   -- month, year, week
  sub_description TEXT DEFAULT '',     -- e.g. "Monthly Retainer"
  stripe_subscription_id TEXT DEFAULT '',
  sub_status TEXT DEFAULT 'none'        -- none, pending, active, cancelled
);
```

## New Endpoints

### Invoice CRUD (existing + extended)
- **GET /api/invoices** — List all invoices
- **POST /api/invoices** — Create invoice (now accepts `has_subscription`, `sub_amount`, `sub_interval`, `sub_description`)
- **GET /api/invoices/<id>** — Get single invoice
- **PUT /api/invoices/<id>** — Update invoice (now editable fields include subscription fields)
- **POST /api/invoices/<id>/send** — Send invoice email with TWO Stripe links:
  - One-time payment link for the setup fee
  - Subscription checkout link for the recurring payment

### Cancel Subscription
**POST /api/invoices/<id>/cancel-subscription**
1. Cancels the Stripe subscription (sets `cancel_at_period_end`)
2. Marks `sub_status = 'cancelled'` in DB
3. **Emails Emilio** with full details so he can take the client's website down

## Dashboard Integration (Invoices Tab)
- Invoice table now shows: **Invoice # | Client | Setup | Subscription | Status | Date | Actions**
- Subscription column shows: `$197.00/month 🟢 active` or `🔴 cancelled`
- "🚫 Cancel Sub" button in Actions when subscription is active
- **Create Invoice modal** redesigned with two sections:
  - **💼 One-Time Setup Fee** (name, email, amount, description, due date)
  - **🔄 Subscription (Optional)** — toggle to reveal: sub amount, billing interval, description
  - Toggle switch for "Add recurring subscription?"

## Cancel Subscription Flow
1. Dashboard: click "🚫 Cancel Sub" next to an active subscription
2. Confirmation dialog
3. Backend: Stripe `cancel_at_period_end` + DB update + **email to Emilio**
4. Email template includes: client name, email, invoice number, setup amount, subscription amount/interval, Stripe sub ID, timestamp
5. Emilio gets the email → manually takes down the client's website/automation

## Stripe Webhook Events
- **checkout.session.completed**:
  - `payment_type: "setup"` → mark invoice as paid
  - `payment_type: "subscription"` → save `stripe_subscription_id`, set `sub_status: active`
- **customer.subscription.deleted** → `handle_subscription_deleted()` → DB update + email Emilio
- **invoice.payment_succeeded** → `handle_subscription_payment()` → notify Emilio of recurring payment

## Invoice Page (Public View)
At `/invoice/<id>`:
- Shows both setup fee and subscription line items
- Subscription section with amount/interval, sub_status badge
- Status badges for both payment and subscription

## What NOT to Change
- Don't break existing lead capture, payment, or dashboard
- Don't change existing endpoints or Plan pricing
- Keep the same dark theme and styling
