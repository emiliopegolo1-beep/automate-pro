# Automate Pro — Invoice System Build Spec

## Overview
Add a professional invoice system to Automate Pro. Generate, send, and track invoices from the dashboard.

## What to Build

### 1. Database — Invoices Table
```sql
CREATE TABLE IF NOT EXISTS invoices (
  id TEXT PRIMARY KEY,
  lead_id TEXT,
  client_name TEXT NOT NULL,
  client_email TEXT NOT NULL,
  amount REAL NOT NULL,
  description TEXT DEFAULT '',
  status TEXT DEFAULT 'draft', -- draft, sent, paid, overdue, cancelled
  due_date TEXT,
  invoice_number TEXT UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  paid_at TIMESTAMP
);
```

### 2. New Endpoints

**GET /api/invoices** — List all invoices (auth required)
**POST /api/invoices** — Create invoice (auth required)
  Accepts: {lead_id, client_name, client_email, amount, description, due_date}
  Auto-generates invoice number (INV-001, INV-002...)

**GET /api/invoices/<id>** — Get single invoice details

**PUT /api/invoices/<id>** — Update invoice (mark paid, change status)

**GET /invoice/<id>** — Public invoice view page (no auth needed — client can view)
  Clean, professional invoice page they can print or save as PDF.

**POST /api/invoices/<id>/send** — Send invoice via email (auth)
  Sends professional invoice email to client with link to invoice page.

### 3. Dashboard Integration
In the admin dashboard:
- New **Invoices** tab in sidebar nav alongside Dashboard, Revenue, Pipeline
- Invoice list view: table with invoice #, client, amount, status, date
- Status badges: Draft (gray), Sent (blue), Paid (green), Overdue (red), Cancelled (gray)
- "Create Invoice" button → modal form
- From lead detail modal: "Create Invoice" button that pre-fills client name/email
- Inline status change (click status to mark paid)

### 4. Invoice Page Design
The public invoice view at /invoice/<id> should:
- Clean, professional design — looks like a real invoice
- Company: Automate Pro
- Client name, email
- Invoice number, date, due date
- Line item: description + amount
- Total
- Status badge
- Printable (print button or @media print CSS)
- Dark theme matching the rest of the site

### 5. Invoice Email Template
Subject: "Invoice #[number] from Automate Pro"
Body brief, professional:
```
Hi [Client],

Your invoice #[number] for [amount] is ready.

View invoice: https://[domain]/invoice/[id]
Due: [due_date]

Thanks,
Emilio
Automate Pro
```

### 6. What NOT to Change
- Don't break existing lead capture, payment, or dashboard
- Don't change existing endpoints

## Technical
- All backend code in server.py
- Invoice page HTML inlined (like the dashboard)
- Use same colors/fonts as rest of site
- Invoice numbers: sequential, format INV-0001
