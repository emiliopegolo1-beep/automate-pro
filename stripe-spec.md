# Automate Pro — Stripe Payment Integration Build Spec

## Overview
Add Stripe payment processing to the Automate Pro business system. Handle one-time payments and monthly subscription retainers.

## What Exists
- server.py running on port 5002 with lead capture, dashboard, Gmail integration
- index.html landing page with service tiers
- leads.db SQLite database
- Gmail API for sending emails

## Stripe Keys (test mode)
Publishable: pk_test_51TK80P1G3bQUr14ujLFblQFAstr7b84jxrKVDAeQ1BWnsCLvHMNxANOnRwxQwUTtNUEtOrzKl0wv9GEzpehVN2ka00rLT737EN
Secret: sk_test_51TK80P1G3bQUr14uFcQ8s0rbId5QhlW29ak1nCVmZKILRcXnv2nEu9Rw60rwRLR8Wj2BIMxAgZMS6lEBIYdjuy6e00Ev3sxl0m

## What to Build

### 1. Stripe Configuration in server.py
Add at the top of server.py (after imports):
```python
import stripe
stripe.api_key = "sk_test_51TK80P1G3bQUr14uFcQ8s0rbId5QhlW29ak1nCVmZKILRcXnv2nEu9Rw60rwRLR8Wj2BIMxAgZMS6lEBIYdjuy6e00Ev3sxl0m"
```

### 2. New Endpoints

**GET /api/config** - Returns public Stripe key:
```json
{"publishableKey": "pk_test_51TK80P1G3bQUr14ujLFblQFAstr7b84jxrKVDAeQ1BWnsCLvHMNxANOnRwxQwUTtNUEtOrzKl0wv9GEzpehVN2ka00rLT737EN"}
```

**POST /api/create-checkout-session** - Creates a Stripe Checkout Session:
Accepts JSON: {price_id, lead_id, success_url, cancel_url}
- price_id is one of the product price IDs
- Returns: {sessionId, url}

**POST /api/stripe-webhook** - Handles payment success/failure events:
- On checkout.session.completed: update lead status to 'closed_won', store payment amount, send email notification to Emilio
- On checkout.session.expired: log the event

### 3. Products and Prices
Create these products and price IDs in Stripe dashboard (Emilio will set these up):

**Starter Plan:**
- Product: "Automate Pro - Starter"
- Setup fee (one-time): $497
- Monthly retainer: $197/month

**Growth Plan:**
- Product: "Automate Pro - Growth"
- Setup fee (one-time): $997
- Monthly retainer: $497/month

**Scale Plan:**
- Product: "Automate Pro - Scale"
- Setup fee (one-time): $1,997
- Monthly retainer: $997/month

BUT - since we're in test mode, we don't actually need real products. Create them programmatically when the server starts:

```python
def create_test_products():
    """Create test products/prices in Stripe if they don't exist."""
    # Check if products already exist (cache in memory)
    # Create: Starter Setup ($497), Starter Monthly ($197/mo)
    # Create: Growth Setup ($997), Growth Monthly ($497/mo)
    # Create: Scale Setup ($1997), Scale Monthly ($997/mo)
    # Store IDs in memory for checkout session creation
```

### 4. Landing Page Update
Update index.html to:
- Add "Buy Now" / "Subscribe" buttons to each service tier
- Load Stripe.js from https://js.stripe.com/v3/
- When clicked, call /api/create-checkout-session and redirect to Stripe Checkout
- After successful payment, redirect to a thank-you page

### 5. Database Update
Add a payments table:
```sql
CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  lead_id TEXT,
  stripe_session_id TEXT,
  amount REAL,
  currency TEXT DEFAULT 'usd',
  plan_name TEXT,
  payment_type TEXT, -- 'setup' or 'subscription'
  status TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 6. Dashboard Revenue Update
The existing dashboard revenue section should now show:
- Total revenue (from payments table)
- This month revenue
- Payment history list

### 7. Checkout Success Page
Simple HTML page at /checkout/success showing:
- "Payment Successful!"
- "We'll be in touch within 24 hours to get you set up."
- Link back to homepage

### 8. What NOT to Change
- Don't break existing /api/lead endpoint
- Don't break the dashboard login
- Don't remove any existing functionality

## Implementation
Write all changes to /Users/emiliopegolo/automate-pro/server.py
Update /Users/emiliopegolo/automate-pro/index.html with payment buttons
When done say: PAYMENTS_READY
