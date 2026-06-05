# Automate Pro — Complete UI Redesign

Using the **ui-ux-pro-max** design intelligence system. Based on search results:

## Design System (from ui-ux-pro-max)

**Style:** Dark Mode (OLED) — dark theme, high contrast, deep black, midnight blue
**Color Palette (Fintech/Analytics):** 
- Primary: #F59E0B (gold/amber trust)
- Background: #0F172A (deep navy)
- Text: #F8FAFC (high contrast white)
- CTA: #22C55E (emerald green for positive actions)
- Notes: Gold for premium feel, green for success states

**Typography:** Fira Code + Fira Sans — technical, precise, designed for dashboards/admin panels
- Fira Code for data, technical elements, tables
- Fira Sans for labels, navigation, body text
- Google Fonts URL: https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap

**UX Rules (from skill):**
- NO emoji icons — use SVG (Lucide/Heroicons) 
- cursor-pointer on all clickable elements
- Hover states with smooth transitions (150-300ms)
- Touch targets min 44x44px
- Visible focus states for keyboard nav
- prefers-reduced-motion respected

## What to change in server.py

### 1. TYPOGRAPHY (both dashboards)
Replace `<link href="...Inter...">` with:
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
```
CSS: `font-family: 'Fira Sans', -apple-system, sans-serif` (body), `font-family: 'Fira Code', monospace` (mono/data)

### 2. ICONS — Replace ALL with SVG
Add an SVG icon helper at top of each dashboard's <style>. Use inline SVGs styled consistently:
- `stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"`
- viewBox="0 0 24 24", width/height="18"

Replace these across both DASHBOARD_HTML and CLIENT_PORTAL_DASHBOARD_HTML:
- `&times;` → SVG X (close) icon
- `→` → SVG arrow-right icon
- `▼` / ▼ → SVG chevron-down icon  
- `✅` → SVG check icon
- `⭐` → SVG star icon
- `💰` → SVG dollar-sign icon
- `⚡` → SVG zap icon
- `🔥` → SVG flame icon
- Sidebar nav links: add SVG icons (layout-dashboard, users, file-text, settings, log-out)
- All close buttons: SVG X icon

### 3. COLORS (both dashboards)
Current → New:
- `--bg: #0a0a0f` → `--bg: #0F172A` 
- `--surface: #12121a` → `--surface: #1E293B`
- `--surface-2: #1a1a26` → `--surface-2: #334155`
- `--border: #2a2a3a` → `--border: rgba(255,255,255,0.08)`
- `--accent: #ff8c42` → `--accent: #F59E0B` (gold)
- `--accent-hover: #e07a30` → `--accent-hover: #D97706`
- `--green: #00d4aa` → `--green: #22C55E`
- `--red: #ff4d6a` → `--red: #EF4444`
- Keep --blue, --yellow, --pink as-is but align with palette

### 4. LOGIN_PAGE
The login page was already partially updated (DM Sans → switch to Fira Sans/Fira Code).
Refine: copper/gold accent, clean login card, proper SVG lock icon (already done partially).

### 5. KEEP IDENTICAL
- All `id=` attributes (JS hooks)
- All Jinja2 template variables `{{var}}`
- All Python logic
- All backend JS functionality
- The `"""` string markers and variable names

## Files to modify
- `/Users/emiliopegolo/automate-pro/server.py`
- DASHBOARD_HTML starts at line 2355, ends line 3732
- CLIENT_PORTAL_DASHBOARD_HTML starts at line 760, ends line 990
- LOGIN_HTML starts at line 2267, ends line 2352

## Verify
After changes: `cd /Users/emiliopegolo/automate-pro && python3 -c "import server; print('OK')"`
