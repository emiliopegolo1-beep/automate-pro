# Automate Pro — Client Portal System

## Overview
Separate Emilio's leads (people wanting automation) from client leads (Bob's customers).
Each client gets their own portal to view their leads.

## What Exists
- Leads database with: id, name, email, phone, business_type, message, status, requirements, quoted_price, source, notify_email, created_at
- Server.py with dashboard, invoices, Stripe

## What to Build

### 1. Lead Source Separation
Add a `source` field that separates:
- "automate_pro" — People contacting Emilio for automation services (shown in Emilio's dashboard)
- "client:[client_name]" — People contacting a client's business (shown in their portal)

When a lead comes in from Bob's website, source = "client:bob"
When a lead comes in from Emilio's website, source = "automate_pro"

### 2. Client Portal Route: GET /portal/<client_id>
Each client gets their own portal page showing ONLY their leads.

Example: GET /portal/bob → Shows Bob's Plumbing leads only

The portal page:
- Clean, simple dashboard
- Header: "Bob's Plumbing — Lead Dashboard"
- Table: Name, Phone, Email, Service, Date, Status
- Simple stats: "Total Leads: X | This Month: Y"
- Password protected (each client gets their own password)
- Client passwords stored in a JSON config file or env vars

### 3. Client Passwords Config
Create client credentials:
```json
{
  "bob": {"name": "Bob's Plumbing", "password": "plumb2026", "notify_email": "bob@bobsplumbing.com"},
  "template": {"name": "Client Name", "password": "changeme", "notify_email": "client@email.com"}
}
```

### 4. Emilio's Dashboard Filter
Emilio's main dashboard should default to showing only "automate_pro" leads.
Add a toggle: "My Leads" / "All Clients" so he can still manage everything when needed.

### 5. Lead Capture Update
When a lead comes in through /api/lead:
- If it has a `notify_email` → source = "client:[client_name]"
- If no `notify_email` → source = "automate_pro"

The notification email should include the client portal link:
"View all leads: https://automate-pro-production.up.railway.app/portal/[client_id]"

## Output
Modify /Users/emiliopegolo/automate-pro/server.py to include all changes.
When done say: PORTAL_READY
