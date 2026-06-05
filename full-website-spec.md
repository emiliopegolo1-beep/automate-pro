# Automate Pro — Complete Rebuild Spec

## Architecture
- **Frontend:** Vite + React (separate from Flask)
- **Backend:** Existing Flask server.py (unchanged, serves API)
- **Deployment:** Railway — Flask serves the built React frontend as static files
- **Animations:** Framer Motion + 21st.dev component patterns
- **Design:** ui-ux-pro-max system (Fira Sans/Code, gold #F59E0B, deep navy)

## Pages (React Router)

### 1. Landing Page (`/`)
- Hero with animated badge, gradient text, staggered reveal elements
- Problem/Solution cards with spring hover effects
- Services/pricing with AnimatePresence hover cards
- How it Works with staggered scroll reveals
- Testimonials carousel with drag support
- CTA section with magnetic button effect
- Animated particle/glow background

### 2. Services Page (`/services`)
- Detailed service breakdown with expandable cards
- Interactive pricing comparison table
- Case study carousel

### 3. About Page (`/about`)
- Company story with scroll-triggered reveals
- Team/approach section with counters
- Stats animation (count-up)

### 4. Contact Page (`/contact`)
- Contact form that POSTs to Flask `/api/lead`
- Animated form validation (shake on error)
- Map/address card with reveal

### 5. AI Agent (Global — side panel)
- Floating chat button (bottom-right) with glow pulse
- Slide-out chat panel with AnimatePresence
- Messages animate in with staggered variants
- Sends messages to Flask AI endpoint
- Typing indicator with bouncing dots animation

## Visual System (from ui-ux-pro-max)

**Typography:** Fira Sans (body) + Fira Code (data/mono)
**Colors:** 
- `--bg: #0F172A`, `--surface: #1E293B`, `--surface-2: #334155`
- `--accent: #F59E0B`, `--accent-hover: #D97706`
- `--green: #22C55E`, `--red: #EF4444`, `--blue: #3B82F6`
- `--text: #F8FAFC`, `--text-muted: #94A3B8`

**Icons:** Lucide React (npm install lucide-react)
**Components:** shadcn/ui-style components built from scratch

## Animation Patterns (from motion-framer skill)

- **Page transitions:** Fade + slide with AnimatePresence
- **Hero:** Staggered children with spring physics
- **Cards:** whileHover scale + shadow, whileTap press
- **Scroll reveals:** whileInView with staggerChildren
- **Form fields:** AnimatePresence error messages with shake
- **Modal/chat:** AnimatePresence with spring exit
- **Counters:** useSpring for smooth number animation
- **Layout shifts:** layout prop on grid elements

## Implementation

Build the React app in `/Users/emiliopegolo/automate-pro-react/`
After building, Flask serves the static files from the build output.

Key dependencies:
- react, react-dom, react-router-dom
- framer-motion
- lucide-react
- vite

The Flask backend already has the API routes needed:
- POST `/api/lead` — contact form
- GET `/api/lead/<id>` — get lead
- All existing routes stay untouched
