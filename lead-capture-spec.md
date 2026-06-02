# Lead Capture Automation Demo — Build Spec

## Overview
Build a working lead capture automation system for the Automate Pro agency. When someone fills out the form on the landing page, it auto-responds and notifies Emilio.

## What to Build

### 1. Update the Landing Page Form
Replace the placeholder contact form on `/Users/emiliopegolo/automate-pro/index.html` with a form that submits to a Flask backend.

The form fields:
- Name (required)
- Email (required)
- Business Type (dropdown: Trades & Construction, Real Estate, Gym/Fitness, E-commerce, Other)
- Message (optional textarea)

On submit:
- Show a loading spinner
- On success: "Thanks [Name]! We'll be in touch within 24 hours."
- On error: show error message

### 2. Flask Backend (new file)
Create `/Users/emiliopegolo/automate-pro/server.py`

A simple Flask server with one endpoint:

**POST /api/lead**
- Accepts JSON: `{name, email, business_type, message}`
- Generates a lead ID
- Saves lead to a local SQLite database (leads.db)
- **Auto-responds** to the lead using Gmail API:
  - From: Emilio's Gmail
  - To: the lead's email
  - Subject: "Thanks for reaching out, [Name]!"
  - Body: Professional thank-you email mentioning their business type and saying we'll schedule a free discovery call within 24 hours
- **Notifies Emilio** by sending an email to emilio.pegolo1@gmail.com:
  - Subject: "🚀 New Lead: [Name] - [Business Type]"
  - Body: Full lead details including name, email, business type, message, timestamp
- Returns success JSON

### 3. Gmail Integration
Reuse the existing Gmail module at `/Users/emiliopegolo/gmail-bot/gmail.py`
Import and call `send_email()` for both auto-response and notification.

### 4. SQLite Database
Simple leads table:
```sql
CREATE TABLE leads (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  business_type TEXT,
  message TEXT,
  auto_responded INTEGER DEFAULT 0,
  notified INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5. Run Instructions
Flask should run on port 5002 (separate from StockVue on 5001).
Update the landing page form action to `http://localhost:5002/api/lead`
Add CORS support to the Flask server.

## Design Notes
- Keep it simple — this is a demo to show clients, not production
- Error handling: if Gmail send fails, still save the lead
- Add a /api/leads endpoint to view all leads (GET only)
- Make it look clean from the terminal when running

## Files to Create/Modify
- MODIFY: `/Users/emiliopegolo/automate-pro/index.html` (update the form)
- CREATE: `/Users/emiliopegolo/automate-pro/server.py` (Flask backend)
- REUSE: `/Users/emiliopegolo/gmail-bot/gmail.py` (Gmail functions)

## Auto-Reply Email Template
Subject: "Thanks for reaching out, [Name]!"
Body:
```
Hi [Name],

Thanks for reaching out about automating your [Business Type] business!

We specialize in building custom AI workflows that handle your leads, bookings, follow-ups, and admin — so you can focus on the work that pays.

I'll review your request and we'll schedule a free 15-minute discovery call within 24 hours to map out exactly what you need.

Looking forward to connecting,

Emilio
Automate Pro
```

## Notification Email Template
Subject: "🚀 New Lead: [Name] - [Business Type]"
Body:
```
New Lead Captured!
━━━━━━━━━━━━━━━━━━━━━━━
Name: [Name]
Email: [Email]
Business: [Business Type]
Message: [Message]
Time: [Timestamp]
━━━━━━━━━━━━━━━━━━━━━━━
Auto-response sent: Yes
```
