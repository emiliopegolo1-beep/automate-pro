# Automate Pro — Dashboard Redesign Spec

## Aesthetic Direction: "Editorial Terminal"

Think Bloomberg Terminal meets Monocle magazine — dark but refined, warm copper tones, Swiss grid discipline, with proper iconography throughout. No generic AI aesthetics.

## What to Change

### 1. Typography
- **Replace Inter** with **DM Sans** (headings/UI) — more distinctive, geometric character
- Keep **JetBrains Mono** for data/code (already loaded in stockvue but not here)
- Improved hierarchy: tighter letter-spacing on labels, refined font sizes

### 2. Icons (CRITICAL)
Replace ALL unicode/emoji symbols with inline SVG icons:
- `&times;` → X icon (SVG)
- `→` → Arrow right icon
- `⚡` → Zap icon
- `🔥` → Flame icon (keep but as SVG)
- `💰` → Dollar sign icon
- `✅` → Check icon
- `⭐` → Star icon
- `▼` → Chevron down icon
- All close buttons: SVG X icon
- Sidebar nav items: proper SVG icons instead of unicode
- Add Lucide-style inline SVG icon set at the top of the CSS

### 3. Color System
- Base: `#07070b` (deeper, warmer than current #0a0a0f)
- Surface: `#0c0c14` → `#12121c` → `#181828` (3 tiers)
- Accent: `#c9732e` (warm copper, less gamery than bright orange)
- Accent glow: `rgba(201,115,46,0.15)`
- Green: `#00c99a` (slightly toned down)
- Red: `#f04a5a`
- Blue: `#4080ff`
- Borders: even more subtle (`rgba(255,255,255,0.04)` → `0.06` → `0.10`)
- Text: `#e8e8f0` (primary), `#8888a0` (muted), `#555570` (dim)

### 4. Visual Refinements
- Add subtle grain/noise overlay to key surfaces (CSS-only via tiny SVG data URI)
- Card hover: lift 1px, subtle glow on accent border
- Sidebar: more breathing room, active state with left bar + subtle bg
- Stats cards: larger numbers, refined label styling
- Kanban: slightly rounded corners, better spacing
- Tables: cleaner rows, better alignments
- Modals: smoother animation, better padding

### 5. Layout Tweaks
- Tighter sidebar padding
- Better responsive breakpoints
- Improved scrollbar styling
- Better focus states for accessibility

## Implementation Notes

- All changes go in `server.py` — the DASHBOARD_HTML string (admin dashboard)
- Also update CLIENT_PORTAL_DASHBOARD_HTML string  
- Replace the entire HTML template strings, don't try to patch inline
- Keep all Python/JS logic identical — only change HTML/CSS
- Inline SVG icons (no external dependencies)
- Self-contained HTML as it currently is
- Test: no broken functionality, all IDs and JS hooks preserved

## Icon SVG Set Needed

Include these icons inline as <svg> elements (Lucide-style, ~16-18px):
- Layout Dashboard (grid)
- Users (lead icon)
- FileText (invoices)
- Settings
- LogOut
- X (close)
- ChevronDown
- ArrowRight
- Check
- DollarSign
- Zap
- Flame
- Star
- Mail
- Phone
- Calendar
- TrendingUp
- MoreHorizontal (kanban menu)
- Download
- Search
- Bell
- AlertCircle

Use a consistent stroke-width=2, stroke-linecap="round", stroke-linejoin="round"
