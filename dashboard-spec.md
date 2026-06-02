# Automate Pro — Full Business System

## What We Have
- Landing page with lead capture form
- Gmail integration for auto-responses
- Flask backend with SQLite

## What We're Building

### Phase 1: Admin Dashboard (this build)
A web dashboard at http://localhost:5002/dashboard that shows:
- Lead list with status pipeline: New → Contacted → Proposal Sent → Closed Won → Closed Lost
- Revenue summary (total earned, pending, this month)
- Client list
- Quick actions: send follow-up email, mark status, add notes
- Password protected (simple auth for now)

### Phase 2: Stripe Payments
- Stripe Checkout integration for collecting payments
- Subscription management (monthly retainers)
- Payment confirmation emails

### Phase 3: Proposal Generator
- Auto-generate PDF proposals
- Pricing calculator based on services selected

### Phase 4: Calendar Booking
- Google Calendar integration for discovery calls
- Booking link to send to leads

---

# Phase 1 — Admin Dashboard Build Spec

## What to Build
Add an admin dashboard to the existing server.py at `/Users/emiliopegolo/automate-pro/server.py`

### 1. Database Schema Updates
Add to leads.db:
```sql
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
);
```

Status values: 'new', 'contacted', 'proposal_sent', 'closed_won', 'closed_lost'

### 2. New Endpoints

**GET /api/dashboard**
Returns overall stats:
- Total leads
- Leads by status
- Revenue this month
- Total revenue
- Recent activity

**PUT /api/lead/<id>**
Update lead status, notes, phone, revenue.

**POST /api/lead/<id>/send-email**
Send a custom email to a lead through Gmail API.

**GET /dashboard**
Renders a full admin dashboard HTML page.

### 3. Dashboard Page (/dashboard)
A professional dark-themed admin panel. Sections:

**Header:**
- Automate Pro logo (text)
- "Admin Dashboard" title
- Logout button (simple)

**Stats Bar:**
- Total Leads (number)
- New This Week (number)
- Revenue This Month ($)
- Conversion Rate (%)

**Pipeline View:**
Kanban-style board showing leads by status column:
- New | Contacted | Proposal Sent | Won | Lost
Each column shows count and lead cards with name, business type, date.

**Lead Detail Modal:**
Click a lead → modal shows:
- Full lead info
- Status dropdown
- Notes textarea
- Quick actions: Send Follow-up Email, Mark as Contacted, Close Won/Lost

**Revenue Section:**
- Total earned
- This month
- By client (lead name + amount)

### 4. Authentication
Simple: hardcoded password in server.py config:
```python
DASHBOARD_PASSWORD = "automate2026"
```
Login page at /login, cookie-based session.

### 5. Technical Details
- All new code goes in server.py
- Dashboard HTML inlined in Python (string template)
- Keep the same dark theme (#0a0a0f, #ff8c42 accent, #00d4aa green)
- Use the same Inter font
- Mobile responsive
- No JavaScript frameworks — vanilla JS + CSS

### 6. What NOT to Change
- Don't break /api/lead endpoint
- Don't touch the landing page form
- Don't change gmail.py integration

## Output
Modify /Users/emiliopegolo/automate-pro/server.py to include all new endpoints and dashboard.
