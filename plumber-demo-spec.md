# Plumber Demo — Client Automation Package

## Scenario
A plumber named "Bob" needs:
1. A simple website showing his services
2. A "Get a Quote" form that emails him new leads
3. Auto-reply to customers who submit
4. Google Calendar booking link for estimates
5. His website hosted so customers can find it

## What to Build
Build a plumber demo page that lives on Automate Pro at /demos/plumber

### The Page (Plumber Website)
Professional one-page website for "Bob's Plumbing Services":
- Hero: "Sydney's Trusted Plumbing Service — Fast, Reliable, 24/7"
- Services section: Emergency Repairs, Hot Water, Blocked Drains, Gas Fitting, Leak Detection
- "Get a Free Quote" form: Name, Phone, Email, Service Type dropdown, Message
- Contact info: phone, email (owned by the plumber — in demo, it's emilio.pegolo1@gmail.com)
- Google Business / trust signals
- Clean, professional design matching the dark theme

### Form Integration
When someone submits the form:
1. Lead saved to Automate Pro database ✅ (existing)
2. Email sent to plumber (emilio.pegolo1@gmail.com for demo): 
   "🚀 New Plumbing Lead: [Name] - [Service]"
3. Auto-reply to customer:
   "Thanks [Name]! We'll call you within 1 hour to schedule your free estimate."
4. Lead appears in Automate Pro dashboard under "New"
5. Plumber clicks "🤖 Build Now" → copy requirements → tell Jarvis to build

### What to Build Code-Wise
Add to server.py:
- New route: GET /demos/plumber — renders the plumber landing page
- The page uses the same backend /api/lead endpoint for form submission
- All CSS inline, dark theme, responsive

### Output
Modify /Users/emiliopegolo/automate-pro/server.py to add the /demos/plumber route.
When done say: PLUMBER_DEMO_READY
