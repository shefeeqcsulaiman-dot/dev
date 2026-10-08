# TaxFlow Design System

This document specifies the visual design of every screen in TaxFlow so a
designer can reproduce it exactly (in Figma or from scratch) without needing
to read the code. It covers colors, typography, spacing, and every reusable
component (buttons, cards, tables, forms, modals, badges, navigation).

The app is **not** a single unified design system — it's four related but
visually distinct "sub-brands" that grew independently. Each is documented
separately below, with a shared "Core App" section first since it's the
largest and most complete.

---

## 0. The Four Sub-Brands

| Sub-brand | Pages | Heading font | Body font | Theme |
|---|---|---|---|---|
| **Core App** | Main Dashboard (`index.html`), HRMS Portal (`hrms.html`) | Syne | DM Sans | Light/Dark toggle |
| **Standalone Auth/Admin** | Login, Signup, ESS Portal, Super Admin | Space Grotesk | DM Sans | Mostly dark (ESS is light) |
| **POS Terminal** | `pos.html` | System UI stack | System UI stack | Light, retail-style |
| **Digital Invoice** | `digital-invoice.html` | Arial/Helvetica | Arial/Helvetica | Light, print-document style |

All four share one convention: **icons are always inline SVG**, never an icon
font or image sprite — stroke-based, `viewBox="0 0 16 16"`, `stroke-width="1.5"`,
`fill="none"`, using `currentColor` so they inherit their container's text color.
There is no charting library anywhere in the app — charts are hand-rolled
inline `<svg>` bar/line charts in the same stroke style.

---

## 1. Core App (Main Dashboard + HRMS Portal)

This is the primary design system — the largest, most consistent, and the
one to default to for any new screen unless there's a reason to match one of
the other three sub-brands.

### 1.1 Fonts

- **Headings / brand:** `Syne`, weights 400/500/600/700
- **Body:** `DM Sans`, weights 300/400/500
- **Monospace (numbers, codes, IDs):** `DM Mono`, weights 400/500
- Loaded via Google Fonts `<link>` with `preconnect` to `fonts.googleapis.com`
  and `fonts.gstatic.com`.
- Base body font size: **13.5px**

### 1.2 Color Tokens

All colors are CSS custom properties on `:root`, then **overridden per theme**
via `body.theme-light` / `body:not(.theme-light)` selectors — so the same
token names resolve to different values depending on light/dark mode.

**Dark mode** (default):
```
--bg:        #0d1117   (page background)
--bg2:       #161b27   (sidebar/topbar background)
--bg3:       #1c2333   (input backgrounds)
--surface:   #1e2840   (card background)
--surface2:  #16202f   (nested surface)
--surface3:  #253050   (scrollbar thumb, hover surface)
--border:    rgba(100,145,222,0.12)
--border2:   rgba(100,145,222,0.20)
--border3:   rgba(100,145,222,0.32)
--text:      #e4e8f0   (primary text)
--text2:     #8892a4   (secondary/muted text)
--text3:     #4f5d76   (tertiary/placeholder text)
--accent:    #6491DE   (primary brand blue)
--accent2:   #4f7fd6   (accent hover/darker)
--accent-glow: rgba(100,145,222,0.20)  (active-state background wash)
--green:     #34d399  --green-bg: rgba(52,211,153,0.12)  --green-border: rgba(52,211,153,0.28)
--red:       #f87171  --red-bg: rgba(248,113,113,0.12)   --red-border: rgba(248,113,113,0.28)
--amber:     #fbbf24  --amber-bg: rgba(251,191,36,0.12)  --amber-border: rgba(251,191,36,0.28)
--purple:    #a78bfa  --purple-bg: rgba(167,139,250,0.12)
--teal:      #2dd4bf  --teal-bg: rgba(45,212,191,0.12)
--pink:      #f472b6  --pink-bg: rgba(244,114,182,0.12)
```

**Light mode** (`body.theme-light`):
```
--bg:        #f1f1f1
--bg2:       #ffffff
--bg3:       #e8edf6
--surface:   #ffffff
--surface2:  #f1f1f1
--surface3:  #e4eaf4
--border:    rgba(7,61,127,0.10)
--border2:   rgba(7,61,127,0.17)
--text:      #0c1630
--text2:     #3a4d6e
--text3:     #7a8dad
--accent:    #6491DE   --accent2: #073D7F
--green:     #0f9f6e   --red: #dc4c4c   --amber: #c77800
--purple:    #073D7F   --teal: #0f8f8f   --pink: #cc4d91
```

**Semantic status colors** are always: green = success/active/paid, red =
danger/expired/overdue, amber = warning/pending, purple = AI/special,
blue (accent) = primary actions/info, teal/pink = extra categorical colors
(charts, tags) — this mapping is consistent everywhere in the app.

### 1.3 Radius & Spacing

```
--r:     10px   (small elements — buttons, chips, small badges)
--r-lg:  14px   (cards)
--r-xl:  18px   (modals)
```
No formal spacing scale exists (spacing is ad-hoc per component, mostly
multiples of 2px between 4–24px) — recommend a designer normalize this to an
**8px base grid** (4/8/12/16/20/24/32) when rebuilding, since that's what the
existing values approximate anyway.

### 1.4 Layout Shell

```
┌──────────┬────────────────────────────────────────┐
│          │  Topbar (56px tall, sticky)              │
│ Sidebar  ├────────────────────────────────────────┤
│ 230px    │                                          │
│ wide     │  Page content (max-width ~1200px,        │
│          │  centered, 24-28px padding)               │
│          │                                          │
└──────────┴────────────────────────────────────────┘
```
- **Sidebar** (`.sb`): 230px wide, fixed height 100vh, own scroll, collapsible
  (animates width down via `transition: width .26s ease`). Background uses a
  dedicated `--sb-bg` token distinct from page background (`#575b6d`-ish dark
  slate in dark mode, `#ebeff7` pale blue in light mode) — the sidebar is
  visually a slightly different shade than the main canvas.
- **Sidebar logo block**: 16-18px padding, company logo/icon (34×34px rounded
  square, `#2563eb` blue background with white icon) + brand wordmark in Syne
  19px bold + optional company name below in smaller muted text.
- **Nav items** (`.nav`): flex row, icon + label, 8px/10px padding, 8px radius,
  13px text. Active state (`.nav.on`): tinted background (`--accent-glow`),
  accent-colored text, a **3px inset left border in accent color** (via
  `box-shadow: -3px 0 0 var(--accent) inset` — not a real border, a shadow
  trick), font-weight 500.
- **Topbar**: 56px tall, sticky top, contains page title, search, and
  action icons (calculator, dark mode toggle, AI assistant, notifications).

### 1.5 Buttons

```
.btn       — base: 7px/13px padding, 8px radius, 12.5px/500 weight, no border,
             inline-flex with 5px icon gap, 0.15s transition
.btn-p     — primary: solid var(--accent) bg, white text, darkens on hover
.btn-g     — secondary/ghost: transparent bg, 1px var(--border2) border,
             var(--text2) text; on hover: var(--surface) bg, var(--text) text
.btn-success / .btn-danger / .btn-warn
           — tinted background (var(--green-bg) etc.), matching border and
             text color, no fill — used for semantic secondary actions
.btn-sm    — 4px/10px padding, 11.5px text (compact variant, used in table rows)
.btn:disabled — 0.45 opacity, cursor not-allowed
```

### 1.6 Cards

```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-lg); /* 14px */
  padding: 18px 20px;
}
```
Card header pattern: title (bold, ~15px) + subtitle (muted, ~12px) on the
left, action button(s) on the right, flex row with space-between.

### 1.7 Stat Tiles
Same visual treatment as `.card` but used specifically for KPI numbers:
surface background, border, 14px radius, 16-18px padding. Label is small
muted uppercase text; value is large (18-26px), often color-coded by
semantic meaning (green for "active", red for "expired", etc.)

### 1.8 Tables

```css
.tbl { width: 100%; border-collapse: collapse; }
.tbl th { text-align:left; padding:9-11px 12px; font-size:11-12px;
          font-weight:600; color:var(--text2); text-transform:uppercase;
          letter-spacing:.4px; border-bottom:1px solid var(--border); }
.tbl td { padding:11px 12px; border-bottom:1px solid var(--border);
          vertical-align:middle; }
.tbl tr:hover td { background: rgba(accent, .04); }  /* subtle row hover */
```
No zebra striping — row separation is purely the bottom border, plus a
subtle hover tint.

### 1.9 Badges (`.b`)
Pill-shaped status labels: `display:inline-flex`, 2px/8px padding, 99px
(fully rounded) border-radius, 11px font, weight 500.
```
.b-g (green/success)  .b-r (red/danger)  .b-a (amber/warning)  .b-p (purple)
```
Each variant: tinted background (`var(--X-bg)`) + matching solid text color,
no border by default (Super Admin's variant of this component adds a
matching 1px border too — see §3).

### 1.10 Chips
Similar to badges but used for filter/toggle controls, not status display:
`.chip` — pill shape, 1px border, transparent/surface background, becomes
tinted + accent-colored + accent border when active (`.chip.on`).

### 1.11 Form Fields

```css
.fg  { margin-bottom: 11px; }              /* field group wrapper */
.fl  { font-size:11px; font-weight:600; color:var(--text2);
       letter-spacing:.2px; margin-bottom:4px; display:block; }  /* label */
.fi  { width:100%; height:36px; padding:7px 10px;
       background:var(--bg3); border:1px solid var(--border2);
       border-radius:7px; color:var(--text); font-size:13px;
       font-family:'DM Sans'; outline:none;
       transition:border-color .15s, box-shadow .15s; }
```
- Labels are **always** small, uppercase-weight-but-not-uppercase-cased,
  muted-colored, sitting directly above the input with 4px gap.
- Inputs use the darker `--bg3` background (recessed look) rather than the
  card's `--surface`, so they read as "wells" sunk into the card.
- `select.fi` removes native appearance (`appearance:none`) and gets a
  custom cursor; `textarea.fi` is vertically resizable, min-height 80px.
- Checkboxes: native `<input type=checkbox>`, just recolored via
  `accent-color: var(--accent)`, sized 14×14px — no custom checkbox graphic.

### 1.12 Modals

```css
.overlay { position:fixed; inset:0; background:rgba(0,0,0,.55);
           display:flex; align-items:center; justify-content:center; }
.modal   { background:var(--bg2); border:1px solid var(--border2);
           border-radius:var(--r-xl); /* 18px */ width:580px;
           max-width:calc(100vw - 32px); max-height:calc(100vh - 32px);
           overflow-y:auto; padding:20px 22px 0;
           transform:translateY(10px); transition:transform .2s; }
.modal.on { transform:translateY(0); }        /* slide-up-in on open */
.modal-lg { width:760px; }   .modal-xl { width:1120px; }
```
Close button (`.modal-x`): 28×28px square, 7px radius, absolute top-right
(14px inset), single "×" character, subtle bordered surface button.
Modal footer: buttons right-aligned, cancel (ghost) + primary action.

### 1.13 Icons
16×16 viewBox, `stroke="currentColor"`, `stroke-width="1.5"`, `fill="none"`,
simple geometric line-art (not filled glyphs) — e.g. dashboard = 4 rounded
squares in a grid, people = overlapping circles+curves, calendar = rounded
rect with two tick marks and a horizontal divider line.

---

## 2. Standalone Auth/Admin Pages (Login, Signup, ESS, Super Admin)

These pages don't load the shared stylesheet — each is a fully self-contained
HTML file with its own `<style>` block, so they can load fast without the
~1MB shared app bundle. Visually related to the Core App (same blue accent
family) but with a different heading font and no light/dark toggle (except
ESS, which is light-only).

- **Heading font:** Space Grotesk, weights 600/700/800
- **Body font:** DM Sans (same as Core App)
- **Login/Signup:** dark gradient background (`linear-gradient(145deg,#040d1e,#0a1f4a,#07163a)`
  or similar), centered white card, logo image (not inline SVG — the
  `/logo.png` raster file) above a "Sign in to your account" subtitle.
- **Super Admin:** always-dark theme, near-identical color tokens to Core
  App's dark mode (`--surface:#111827`, `--text:#dde4f0`, `--primary:#4f8ef0`,
  `--red:#f06b6b`, `--green:#3ecf8e`) but re-declared locally rather than
  sharing the token names 1:1. See `docs/design-system.md` §3 below for its
  specific sidebar-nav layout (added when the dashboard was redesigned).
- **ESS Portal:** light theme, `--accent:#1d4ed8`, `--bg:#f1f5f9`, white
  surface cards, otherwise same shape language (rounded cards, pill badges).

---

## 3. Super Admin Dashboard (detailed — most recently built)

The Super Admin dashboard uses a sidebar-nav layout (added in this project),
structurally identical in concept to the Core App's sidebar but restyled to
match Super Admin's own dark palette rather than reusing Core App's CSS.

### 3.1 Colors (as declared in `superadmin.html`)
```
--red:#f06b6b   --green:#3ecf8e   --muted:#8896b3
--border: rgba(99,130,200,.14)   --surface:#0d1120
--text:#dde4f0   --primary:#4f8ef0   --hover: rgba(99,130,200,.06)
```
Body background: `#0a0f1e` (slightly darker than `--surface`).

### 3.2 Shell
```
┌──────────┬──────────────────────────────────────┐
│ Topbar (56px, spans full width, sticky top)      │
├──────────┼──────────────────────────────────────┤
│ Sidebar  │  Page content (max-width 1200px,      │
│ 216px    │  centered, 28px/24px padding)          │
│ (own     │                                        │
│ scroll)  │                                        │
└──────────┴──────────────────────────────────────┘
```
- Sidebar background `#0d1120`, 1px right border, 18px/12px padding,
  `position:sticky; top:56px` so it scrolls independently below the topbar.
- Nav items (`.sb-item`): 9-12px padding, 8px radius, 13px/500 text, icon +
  label + optional trailing count badge (red pill, for error/trial counts).
  Active state: `rgba(79,142,240,.12)` background wash, `#4f8ef0` text —
  same visual language as Core App's `.nav.on` but without the inset
  left-border shadow trick.
- Page sections (`.page-section`) are toggled via `display:none`/`.on`, one
  visible at a time — a client-side single-page-app pattern, not real routing.

### 3.3 Stat Tiles
Grid: `repeat(auto-fit, minmax(160px,1fr))`, 14px gap. Each tile: `.stat-card`
= surface bg, 1px border (sometimes color-tinted per semantic meaning, e.g.
red-tinted border for "Expired"), 12px radius, 18px/20px padding. Label:
11.5px uppercase muted. Value: 26px DM Mono, medium weight — **note DM Mono
is used for stat numbers here even though Super Admin's `<head>` doesn't
otherwise lean on monospace elsewhere; keep this for numeric emphasis.**

### 3.4 Mini Charts (added this session)
No charting library — hand-rolled inline `<svg viewBox="0 0 W H">` bar
charts: bars are `<rect>` elements with `rx="2"`, colored by semantic meaning
(blue for volume, green for growth, red for errors), height proportional to
value, x-axis date labels shown every Nth bar to avoid crowding, `<title>`
tooltip on hover via native SVG title element (no JS tooltip library).

### 3.5 Field/Input Inventory (every input type used in Super Admin modals)
| Input | Used for | Notes |
|---|---|---|
| `<input class="fi">` text | Company name, TRN, email, password | 1.5px border, focus → accent border |
| `<input class="fi mono">` | TRN, IDs | Monospace variant of the same field |
| `<input type="date" class="fi">` | Subscription expiry | Native date picker, same field chrome |
| `<input type="password" class="fi">` | Admin password, authorization passwords | Same chrome; delete-company flow uses **two** password fields as a double-confirmation gate |
| `<select class="fi">` | Role picker, status filter, days-range filter | Native select, custom border/bg only |
| `<div class="mod-grid">` of `<label class="mod-item">` | Module permission toggles | Custom checkbox-card component — see §3.6 |

### 3.6 Module Permission Grid (custom component)
A grid (`repeat(auto-fill, minmax(175px,1fr))`, 8px gap) of clickable
"chip cards" (`.mod-item`), each containing a native checkbox + a label +
sub-label + (new) a usage-count line. Selected state: accent border + tinted
background. This is a bespoke pattern, not reused elsewhere in the app —
worth formalizing as a real component ("toggle card") if rebuilding.

### 3.7 Impersonation Banner (added this session)
Fixed-position bar, full viewport width, `z-index:99999`, solid `#7c3aed`
(purple — distinct from the app's blue accent, chosen specifically so it
reads as "different mode, be careful" rather than a normal notice), white
text, centered content: warning icon + "Viewing as **{user}** at **{company}**
as Super Admin" + white pill button "Exit Impersonation". Page content gets
`padding-top` equal to the banner's rendered height so it doesn't overlap
the topbar underneath.

---

## 4. POS Terminal (`pos.html`)

Deliberately different: a light, high-contrast retail point-of-sale UI
optimized for touch/fast scanning, not an admin dashboard.

```
--bg:#f1f5f9  --sf:#ffffff  --sf2:#f8fafc  --sf3:#f1f5f9
--bd:#e2e8f0  --bd2:#cbd5e1
--t1:#0f172a (primary text)  --t2:#475569  --t3:#94a3b8
--acc:#6366f1  --acc2:#4f46e5  --acc-bg:#eef2ff       (indigo, not blue — deliberately distinct)
--grn:#22c55e --teal:#14b8a6  --red:#ef4444  --amb:#f59e0b  --pur:#8b5cf6
--cart: #0f172a / #1e293b / #334155                    (dark cart panel, contrasts the light main area)
--fn: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif
```
No Google Fonts — relies on the OS system font stack for performance
(this page needs to feel instant at a checkout counter). The cart/receipt
panel is intentionally dark against an otherwise light, airy product-grid
UI — the one place in the whole app that mixes light and dark in the same
screen by design.

---

## 5. Digital Invoice (`digital-invoice.html`)

A print-oriented document view, not an app screen — designed to look like a
formatted paper invoice/PDF rather than a UI.
```
--accent:#2563eb  --ink:#172033  --muted:#667085  --line:#e5eaf2
--paper:#fff      --bg:#f4f7fb   (page canvas around the "paper")
font-family: Arial, Helvetica, sans-serif  (print-safe, no web font loading)
mono: "Courier New", monospace   (for invoice/reference numbers)
```
Content sits on a white "paper" card with a soft shadow against a pale
gray-blue canvas, mimicking a physical printed page.

---

## 6. What a Designer Needs to Know Before Starting

1. **Pick one sub-brand as canon.** The Core App system (§1) is the most
   complete and most "designed" — recommend treating it as the source of
   truth and gradually migrating Login/Signup/ESS/Super Admin onto its exact
   token names, since they're already visually close but not pixel-identical.
2. **There is no spacing scale, type scale, or shadow scale formally
   defined anywhere** — values are consistent by convention/copy-paste, not
   enforced by a system. Formalizing an 8px spacing scale and a defined type
   ramp (11/12/13/13.5/15/18/22/26px, which is what's actually in use) would
   be the single highest-value cleanup if rebuilding this in a proper design
   tool.
3. **Every icon is hand-drawn inline SVG** in a consistent stroke style —
   if migrating to a design tool, recreate these as a proper icon set
   (16×16 grid, 1.5px stroke, round joins) rather than trying to reuse the
   raw SVG paths, most of which were drawn ad-hoc rather than from a shared
   icon library.

---

## 7. Icon Inventory

Complete catalog of every distinct icon in the app, with exact SVG markup so
a designer can recreate each one pixel-for-pixel. All are `fill="none"`,
`stroke="currentColor"` unless noted, `viewBox="0 0 16 16"` with
`stroke-width` between 1.4–1.8 unless noted. Icons visually identical across
files are listed once with every place they're reused.

### 7.1 Sidebar Nav Icons — Main App (`index.html`)

**Dashboard (house/roof)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 8l6-5 6 5"/><path d="M4 7v7h8V7"/><path d="M6.5 14v-4h3v4"/></svg>
```

**Sales & Invoices (shopping bag)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 4h12l-1 8H3L2 4z"/><path d="M5 4V3a3 3 0 016 0v1"/></svg>
```

**Quotations (document with lines)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 2h8l2 2v10H3z"/><path d="M10.5 2v3H13"/><path d="M5 7h6M5 10h5"/></svg>
```

**Point of Sale (register/till)** — also topbar "POS Terminal" button; reused as the POS module's own logo in `pos.html` (with/without dots)
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="3" width="14" height="11" rx="1"/><path d="M1 7h14"/><path d="M5 11h2M10 11h1"/><path d="M8 1v2"/></svg>
```

**Purchases (receipt/tag)** — identical glyph reused for the dashboard's "Add Purchase" button
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 2h2l2 8h8"/><circle cx="7" cy="13" r="1"/><circle cx="13" cy="13" r="1"/></svg>
```

**Inventory (stacked boxes)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 4h12"/><path d="M4 4v10"/><path d="M12 4v10"/><path d="M4 8h8"/></svg>
```

**Expenses (clock)** — identical glyph reused as HRMS "Attendance" nav icon and Super Admin "Audit Log" nav icon
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M8 5v3l2 2"/></svg>
```

**Bank & Payments (bank/pillars)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="6" width="14" height="9" rx="1"/><path d="M1 9h14"/><path d="M8 2L1 6h14L8 2z"/></svg>
```

**Accounting (ledger/spreadsheet)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="12" height="12" rx="1"/><path d="M5 5h6M5 8h6M5 11h4"/></svg>
```

**Corporate Accounting (building/columns)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 14V3h10v11"/><path d="M5 5h2M9 5h2M5 8h2M9 8h2M5 11h2M9 11h2"/></svg>
```

**Reports / Company & Settings (house-outline)** — ⚠️ **duplicate glyph bug**: these two *different* nav items currently share the exact same icon; give them distinct icons when rebuilding
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 14V6l6-4 6 4v8"/><path d="M6 14v-4h4v4"/></svg>
```

**HRMS (person + gear/link)** — near-duplicate reused (white-stroke variant) as the HRMS module's own logo badge
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2a3 3 0 100 6 3 3 0 000-6z"/><path d="M2 14c0-3 2.7-5 6-5s6 2 6 5"/><path d="M12 7l1.5-1.5"/><circle cx="13.5" cy="4.5" r="1.5"/></svg>
```

**Notifications (bell)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 14a2 2 0 002-2H6a2 2 0 002 2z"/><path d="M3 11h10l-1-2V6a4 4 0 10-8 0v3z"/></svg>
```

**Expert Review (clock/target)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="7"/><path d="M8 4v5l3 2"/></svg>
```

**Sidebar hamburger toggle** (20×20, stroke-width 1.8)
```html
<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" width="16" height="16"><path d="M3 5h14M3 10h14M3 15h14"/></svg>
```

**Calculator** (topbar "Calc" button)
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><rect x="1" y="1" width="14" height="14" rx="2"/><path d="M4 4h2M10 4h2M4 8h2M10 8h2M4 12h2M10 12h2"/></svg>
```

**Dashboard Quick Actions row** (7 icons, 20×20 viewBox, stroke-width 1.5):
```html
<!-- Invoice --><svg viewBox="0 0 20 20"><path d="M3 5h14M3 10h14M3 15h8"/></svg>
<!-- Purchase --><svg viewBox="0 0 20 20"><path d="M3 3h14v14H3z"/><path d="M7 7h6M7 11h4"/></svg>
<!-- Quote --><svg viewBox="0 0 20 20"><path d="M5 3h8l2 2v12H5z"/><path d="M8 3v4h5"/></svg>
<!-- Ledger --><svg viewBox="0 0 20 20"><path d="M3 4h14v12H3z"/><path d="M3 9h14M8 9v7"/></svg>
<!-- Reports --><svg viewBox="0 0 20 20"><path d="M4 17V8l4-5 4 5v9"/><path d="M8 17v-5h4v5"/></svg>
<!-- Alerts --><svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7"/><path d="M10 7v3M10 13v1"/></svg>
<!-- POS --><svg viewBox="0 0 20 20"><rect x="2" y="4" width="16" height="12" rx="1"/><path d="M2 8h16"/><path d="M6 12h3M13 12h1"/><path d="M10 2v2"/></svg>
```

### 7.2 Sidebar Nav Icons — HRMS (`hrms.html`)

**Dashboard (2×2 grid)** — identical glyph reused as Super Admin "Overview" nav icon and a topbar "Dashboard" pill in `pos.html`
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="1" width="6" height="6" rx="1.5"/><rect x="9" y="1" width="6" height="6" rx="1.5"/><rect x="1" y="9" width="6" height="6" rx="1.5"/><rect x="9" y="9" width="6" height="6" rx="1.5"/></svg>
```

**Employees (people)** — reused for both "Employees" and "HR Workflow" nav items
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6" cy="5" r="2.5"/><path d="M1 13c0-2.5 2-4 5-4s5 1.5 5 4"/><circle cx="12.5" cy="5" r="1.8"/><path d="M14.5 13c0-2-1.3-3-2.5-3.5"/></svg>
```

**Attendance (clock)** — same base glyph as `index.html` "Expenses"
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M8 5v3.5l2.5 1.5"/></svg>
```

**Rota & Shift (calendar)** — also reused as the topbar date icon
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="11" rx="1"/><path d="M5 1.5v3M11 1.5v3M2 6h12"/></svg>
```

**Leave (umbrella)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 8c0-2.8 2.2-5 5-5s5 2.2 5 5v4H3V8z"/><path d="M1 12h14M6 3.5C6 2 7 1 8 1s2 1 2 2.5"/></svg>
```

**Payroll (banknote)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 6h12M5 10h3M10 10h1"/></svg>
```

**Overtime (clock with extra tick)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M8 5v3.5l2.5 1.5"/><path d="M12 2l2-1.5"/></svg>
```

**Loans & Advances (banknote with arrow)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="4" width="12" height="9" rx="1"/><path d="M2 7.5h12M8 7.5V4M6 11h4"/></svg>
```

**Recruitment/ATS (person + checkmark)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="7" cy="5" r="3"/><path d="M1 14c0-3 2.5-5 6-5s6 2 6 5"/><path d="M13 2l1 1-2.5 2.5-1-1"/></svg>
```

**Performance (star)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2l1.2 3.5H13l-2.9 2 1.1 3.5L8 9.2l-3.2 1.8 1.1-3.5L3 5.5h3.8z"/></svg>
```

**Training (graduation cap)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 13V8a5 5 0 0110 0v5M1 13h14"/><circle cx="8" cy="7" r="2"/></svg>
```

**Assets (stacked bars)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1.5" y="8" width="4" height="5" rx="1"/><rect x="6" y="5.5" width="4" height="7.5" rx="1"/><rect x="10.5" y="3" width="4" height="10" rx="1"/></svg>
```

**Reports & Analytics (line-chart checkmark)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 12l4-4 3 3 5-6"/><path d="M10 6h4v4"/></svg>
```

**AI Insights (sparkle/wand)** — identical glyph also used as the HRMS logo badge (white-on-blue)
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2c1.5 0 3 1 3.5 2.5S11 8 8 9c-3 1-3.5 2-3.5 3.5"/><circle cx="8" cy="13.5" r=".8" fill="currentColor" stroke="none"/></svg>
```

**HR Settings (gear, simple)**
```html
<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="2.5"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.2 3.2l1.5 1.5M11.3 11.3l1.5 1.5M3.2 12.8l1.5-1.5M11.3 4.7l1.5-1.5"/></svg>
```

**Topbar search**
```html
<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6.5" cy="6.5" r="4.5"/><path d="M10 10l3 3"/></svg>
```

**Topbar Notifications (bell, alt style)**
```html
<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 1.5a5 5 0 00-5 5v3L1.5 11.5h13L13 9.5v-3a5 5 0 00-5-5z"/><path d="M6.5 13.5a1.5 1.5 0 003 0"/></svg>
```

**Topbar Leave Requests (envelope)**
```html
<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 3h12a1 1 0 011 1v7a1 1 0 01-1 1H5l-3 2V4a1 1 0 011-1z"/></svg>
```

**Sign Out (door + arrow)**
```html
<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 2H3a1 1 0 00-1 1v10a1 1 0 001 1h3"/><path d="M10 11l3-3-3-3"/><path d="M13 8H6"/></svg>
```

### 7.3 Sidebar Nav Icons — Super Admin

```html
<!-- Companies --><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="8" height="12" rx="1"/><path d="M10 6h4v8h-4M4.5 5h1M4.5 8h1M4.5 11h1"/></svg>
<!-- Usage Analytics --><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 14V2M2 14h12"/><rect x="4" y="8" width="2" height="4"/><rect x="7.5" y="5" width="2" height="7"/><rect x="11" y="9.5" width="2" height="2.5"/></svg>
<!-- System Health --><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M1 8h3l1.5-4L8 12l1.5-6L11 8h4"/></svg>
<!-- Client Errors --><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 1.5l7 12.5H1L8 1.5z"/><path d="M8 6.5v3M8 11.5v.1"/></svg>
<!-- Trial Requests --><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="6" cy="5" r="2.5"/><path d="M1 13c0-2.5 2-4 5-4s5 1.5 5 4"/><path d="M12 4v4M10 6h4"/></svg>
```
("Overview" reuses HRMS's 2×2 dashboard grid; "Audit Log" reuses the clock glyph — see §7.1/7.2.)

### 7.4 Common Action Icons (centralized in `src/app.js`)

Defined once as reusable JS functions and injected into row-action buttons
across every data table app-wide. Recreating just these ~10 covers the vast
majority of table row actions in the whole app:

```html
<!-- Delete (trash) --><svg viewBox="0 0 16 16"><path d="M3 4h10"/><path d="M6 4V2.8h4V4"/><path d="M5 6v7"/><path d="M8 6v7"/><path d="M11 6v7"/><path d="M4.5 4l.5 10h6l.5-10"/></svg>
<!-- View (eye) --><svg viewBox="0 0 16 16"><path d="M1.8 8s2.2-4 6.2-4 6.2 4 6.2 4-2.2 4-6.2 4-6.2-4-6.2-4z"/><circle cx="8" cy="8" r="1.8"/></svg>
<!-- Edit (pencil) --><svg viewBox="0 0 16 16"><path d="M3 11.5V13h1.5L12 5.5 10.5 4 3 11.5z"/><path d="M9.8 4.7l1.5 1.5"/><path d="M2.5 14h11"/></svg>
<!-- Check/confirm --><svg viewBox="0 0 16 16"><path d="M3 8.3l3 3L13 4.7"/></svg>
<!-- Copy (two rects) --><svg viewBox="0 0 16 16"><rect x="5" y="1" width="8" height="10" rx="1.2"/><rect x="2" y="5" width="8" height="10" rx="1.2"/></svg>
<!-- Invoice/receipt image --><svg viewBox="0 0 16 16"><rect x="1.5" y="2" width="13" height="12" rx="1.2"/><path d="M1.5 10.5l3-3 2.5 2.5 2.5-2 3.5 4"/><circle cx="11.5" cy="5.5" r="1.2"/></svg>
<!-- Upload (24x24) --><svg viewBox="0 0 24 24"><path d="M12 15V5"/><path d="M8 9l4-4 4 4"/><path d="M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15"/></svg>
<!-- Download --><svg viewBox="0 0 16 16"><path d="M8 2v7"/><path d="M5 6l3 3 3-3"/><path d="M3 13h10"/></svg>
<!-- Share (network nodes) --><svg viewBox="0 0 16 16"><path d="M6.5 8.5l3-1.8"/><path d="M6.5 7.5l3 1.8"/><circle cx="4.5" cy="8" r="2"/><circle cx="11.5" cy="5.8" r="2"/><circle cx="11.5" cy="10.2" r="2"/></svg>
<!-- Skip/close (thin X) --><svg viewBox="0 0 16 16"><path d="M4 4l8 8"/><path d="M12 4l-8 8"/></svg>
<!-- Mark Paid (thick check, green) --><svg viewBox="0 0 16 16" stroke-width="1.6"><path d="M13 4L6 11l-3-3"/></svg>
```
Modal close buttons use the HTML entity `&times;`, not SVG.

⚠️ **Search icon inconsistency**: two different variants are used in
different places — a curved-tail magnifying glass (POS/HRMS search boxes)
and a straight-line-tail one (ledger/customer search widgets). Unify to one
when rebuilding:
```html
<!-- curved tail --><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/></svg>
<!-- straight tail --><svg viewBox="0 0 16 16" fill="none" stroke-width="2"><circle cx="6.5" cy="6.5" r="4.5"/><line x1="10" y1="10" x2="14" y2="14"/></svg>
```

**Refresh/sync**: `<svg viewBox="0 0 16 16" stroke-width="1.6"><path d="M13.5 8A5.5 5.5 0 112.5 5"/><path d="M2.5 2v3h3"/></svg>`
**Close/Cancel (thick X)**: `<svg viewBox="0 0 16 16" stroke-width="1.6"><path d="M3 3l10 10M13 3L3 13"/></svg>`

### 7.5 Status / Alert Icons (toast notifications, `app.js`)

```html
<!-- Success --><svg viewBox="0 0 16 16" stroke-width="2" stroke-linecap="round"><circle cx="8" cy="8" r="6"/><polyline points="5.5,8 7,9.5 10.5,6"/></svg>
<!-- Warning --><svg viewBox="0 0 16 16" stroke-width="1.8" stroke-linecap="round"><path d="M8 2.5L14 13H2z"/><line x1="8" y1="7" x2="8" y2="10"/><circle cx="8" cy="11.5" r=".6" fill="currentColor" stroke="none"/></svg>
<!-- Error --><svg viewBox="0 0 16 16" stroke-width="2" stroke-linecap="round"><circle cx="8" cy="8" r="6"/><line x1="5.5" y1="5.5" x2="10.5" y2="10.5"/><line x1="10.5" y1="5.5" x2="5.5" y2="10.5"/></svg>
<!-- Info --><svg viewBox="0 0 16 16" stroke-width="2" stroke-linecap="round"><circle cx="8" cy="8" r="6"/><line x1="8" y1="7.5" x2="8" y2="11"/><circle cx="8" cy="5.5" r=".6" fill="currentColor" stroke="none"/></svg>
```

Activity-feed icons (20×20, stroke-width 2): Record Saved (document + tray),
Record Deleted (trash), Record Updated (document + pencil), generic default
(circle + dot) — same visual family as the action icons above, just larger.

### 7.6 Empty-State Icons

```html
<!-- No records (dashed target, 48x48) --><svg viewBox="0 0 48 48" stroke-width="2.5"><circle cx="24" cy="24" r="20"/><line x1="24" y1="14" x2="24" y2="24"/><line x1="24" y1="30" x2="24" y2="33"/></svg>
<!-- Avatar placeholder --><svg viewBox="0 0 24 24" stroke-width="1.4"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>
<!-- Empty cart (POS, 52x52) --><svg viewBox="0 0 24 24" stroke-width="1"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a4 4 0 00-8 0v2"/></svg>
```

### 7.7 POS-Specific Icons

```html
<!-- Location pin --><svg viewBox="0 0 16 16" stroke-width="1.7"><path d="M8 1.5a4 4 0 014 4c0 3-4 9-4 9S4 8.5 4 5.5a4 4 0 014-4z"/><circle cx="8" cy="5.5" r="1.5"/></svg>
<!-- Customer (person) --><svg viewBox="0 0 16 16" stroke-width="1.5"><circle cx="8" cy="5" r="3"/><path d="M2 13c0-3 2-5 6-5s6 2 6 5"/></svg>
<!-- Table/counter --><svg viewBox="0 0 16 16" stroke-width="1.5"><rect x="1" y="7" width="14" height="2"/><path d="M3 9v4M13 9v4M2 7V4a1 1 0 011-1h10a1 1 0 011 1v3"/></svg>
<!-- Credit --><svg viewBox="0 0 16 16" stroke-width="1.5"><rect x="1" y="4" width="14" height="9" rx="1"/><path d="M1 7h14M4 11h3"/></svg>
<!-- Card payment --><svg viewBox="0 0 16 16" stroke-width="1.5"><rect x="1" y="4" width="14" height="9" rx="1"/><path d="M1 7h14M10 11h2"/></svg>
<!-- Split payment --><svg viewBox="0 0 16 16" stroke-width="1.5"><path d="M1 5h10M1 8h7M1 11h4"/><rect x="9" y="6" width="6" height="8" rx="1"/></svg>
```

### 7.8 Login/Signup Feature Icons

24×24 viewBox, stroke-width 2, rounded caps/joins — a slightly heavier style
than the rest of the app, used only for the signup page's feature bullets:
```html
<!-- VAT invoicing --><svg viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
<!-- HR/payroll --><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>
<!-- Inventory --><svg viewBox="0 0 24 24"><path d="M3 3h18v4H3z"/><path d="M3 10h18v4H3z"/><path d="M3 17h18v4H3z"/></svg>
<!-- Reports --><svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
<!-- AI/audit (shield) --><svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
<!-- Show/hide password (eye) --><svg viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
```

### 7.9 Biometric Integration Step Icons (`_BIO_ICONS` in `app.js`)

A distinct, heavier icon set (24×24, `stroke-width="2.5"`, rounded caps/joins)
used only in the HR Settings → Biometric Integration setup-guide flow — a
different visual language from the rest of the app, closer to a generic
"lucide-style" icon set than TaxFlow's own thinner 16×16 icons:
```html
<!-- key --><svg viewBox="0 0 24 24"><circle cx="7.5" cy="15.5" r="5.5"/><path d="M21 2l-9.6 9.6"/><path d="M15.5 7.5l3 3L22 7l-3-3"/></svg>
<!-- monitor --><svg viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
<!-- toggle --><svg viewBox="0 0 24 24"><rect x="1" y="7" width="22" height="10" rx="5"/><circle cx="16" cy="12" r="3" fill="currentColor"/></svg>
<!-- check (circular) --><svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
<!-- download (thick) --><svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
<!-- terminal --><svg viewBox="0 0 24 24"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
<!-- csv --><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>
```

### 7.10 Known Icon Issues (fix these when rebuilding)
1. "Reports" and "Company & Settings" nav items in `index.html` use the
   identical house-outline icon — pick a distinct one for each.
2. Two inconsistent search-icon variants exist (§7.4) — standardize on one.
3. `ess.html` has no icons of its own — it inherits whatever `app.js`
   injects at runtime, or renders icon-free in places.
4. The Biometric Integration step icons (§7.9) are visually heavier/rounder
   than the rest of the app's icon language — intentional or not, a designer
   should decide whether to unify these or keep them as a deliberate "system
   setup wizard" visual accent.
