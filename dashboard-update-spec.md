# Automate Pro — Dashboard Fields & Pipeline Update

## Overview
Update the lead detail modal and pipeline view in the admin dashboard with proper fields for Emilio's business workflow.

## New Fields to Add to Database (server.py init_db)
Add columns to leads table if not exist:
- requirements TEXT DEFAULT ''
- quoted_price REAL DEFAULT 0
- follow_up_date TEXT DEFAULT ''
- source TEXT DEFAULT 'website' (how they found us: website, email, call, walk-in)

## Updated Status Pipeline
Change the lead status values to reflect real workflow:
- 'new' → New lead, no contact yet
- 'call_scheduled' → Discovery call booked
- 'call_done' → Call completed, requirements gathered
- 'building' → I'm building the automation
- 'demo_ready' → Demo ready to show client
- 'delivered' → Automation delivered to client
- 'paid' → Invoice paid, complete

## Lead Detail Modal Updates
In the dashboard, when clicking a lead, the modal should show:

**Section 1 — Lead Info** (read-only)
- Name, Email, Business Type
- Source (website/email/call/walk-in)
- Message
- Created Date

**Section 2 — Workflow** (editable)
- Status dropdown with the new pipeline stages (color coded)
- Requirements (large text area) — "What they need built"
- Quoted Price ($) — number input
- Follow-up Date — date input
- Notes (text area) — any extra info

**Section 3 — Quick Actions**
- 📅 Create Invoice button (opens invoice modal pre-filled)
- 📧 Send Email button
- 🔗 Copy Calendly link
- 📋 Copy requirements summary to clipboard

## Pipeline Board Update
The kanban board columns should now show:
- New | Call Scheduled | Call Done | Building | Demo Ready | Delivered | Paid

Each column shows lead cards with name, business type, price (if quoted), and date.

## Stats Cards Update
Add to the stats bar at the top:
- "Pending Delivery" — count of leads with status building/demo_ready
- "Revenue This Month" — from invoices paid this month
- "Active Clients" — leads with status delivered or paid

## What NOT to Change
- Don't break existing invoice system
- Don't break Stripe payment integration
- Don't break the landing page
- Keep the same dark theme and styling

## Technical
- All changes go in /Users/emiliopegolo/automate-pro/server.py
- Database changes must use ALTER TABLE IF NOT EXISTS (safe migration)
- The dashboard HTML template is in Python string format
- Use the same CSS variables and colors
