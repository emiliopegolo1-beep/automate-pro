# Automate Pro — Client Delivery Protocol

## When Emilio says "Build [client]'s automation"

### 1. Read requirements
Check the Automate Pro database for the client's requirements (from the dashboard).
If not enough details, ask Emilio:
- What exactly does the client need?
- What's their industry?
- What email should notifications go to?

### 2. Design Preferences — CRITICAL
- NO emojis in client-facing websites or emails. Ever.
- Professional, clean, modern design
- Dark theme matching Automate Pro aesthetic (#0a0a0f, #ff8c42 accent)
- Responsive (works on phone)
- Inter font, professional typography
- No cartoonish elements, no "fun" design — look legit

### 3. Build Structure
- Client page goes on Railway (automate-pro-production.up.railway.app)
- Or as standalone HTML for Netlify if client needs their own domain
- Form connects to /api/lead with notify_email set to client's email
- When form submits → email to client + auto-reply to customer

### 4. Test Procedure
Tell Emilio exactly what to test:
1. "Open the page on your phone"
2. "Fill in the form with fake data"
3. "Check your Gmail — you should get the notification"
4. "Check the dashboard — lead should show up"

### 5. Delivery
After testing passes:
- If Netlify: give Emilio the standalone HTML file path
- If Railway: give Emilio the URL
- Tell Emilio: "Client is ready. Send them the invoice."

### 6. The Stack
- Flask backend on Railway
- Gmail API for emails
- Stripe for payments
- Calendly for booking
- Netlify or Railway for hosting
- Claude Code for building the actual code
