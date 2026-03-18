# 🎨 Alchemica Custom Theme Guide

Create your own themes, cursor trails, and background animations — then share them with the community or sell them as supporter packs.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Theme File Format](#theme-file-format)
3. [CSS Variables Reference](#css-variables-reference)
4. [Layout & Structure Overrides](#layout--structure-overrides)
5. [Background Animations](#background-animations)
6. [Cursor Trails](#cursor-trails)
7. [Packaging a Theme Pack (.zip)](#packaging-a-theme-pack-zip)
8. [Importing a Theme](#importing-a-theme)
9. [Supporter Packs](#supporter-packs)
10. [Full Examples](#full-examples)

---

## Quick Start

```json
{
  "id": "my-theme",
  "name": "My Theme",
  "author": "YourName",
  "type": "community",
  "variables": {
    "--bg": "#0d0d1a",
    "--accent": "#ff6600",
    "--text": "#f0e8d0"
  }
}
```

Drop that file into **Settings → Custom Themes** and you're done.

---

## Theme File Format

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier. Lowercase, digits, hyphens, underscores. Max 64 chars. |
| `name` | string | Display name shown in the theme dropdown. |

### Optional fields

| Field | Type | Description |
|-------|------|-------------|
| `author` | string | Your name or handle |
| `type` | string | `"community"` (default) or `"supporter"` — supporter themes show a 💜 badge |
| `variables` | object | CSS variable overrides (see full reference below) |
| `animation_code` | string | Inline background animation JS |

---

## CSS Variables Reference

### Core palette

| Variable | What it affects | Example |
|----------|----------------|---------|
| `--bg` | Main page background colour | `#0d0d1a` |
| `--bg-grad` | Background gradient (overrides --bg) | `linear-gradient(135deg, #0a0a1a, #0d1b2a)` |
| `--accent` | Buttons, links, highlights | `#4a9eff` |
| `--accent-glow` | Glow/shadow on accented elements | `rgba(74,158,255,0.4)` |
| `--text` | Primary text | `#e8eaf6` |
| `--text-dim` | Secondary/hint text | `#7080a0` |
| `--text-bright` | Headings and emphasis | `#ffffff` |
| `--toast-bg` | Notification toast background | `rgba(20,20,40,0.95)` |

### Glass / panels

| Variable | What it affects | Example |
|----------|----------------|---------|
| `--glass-bg` | Item cards and panel backgrounds | `rgba(255,255,255,0.04)` |
| `--glass-border` | Card and panel borders | `rgba(255,255,255,0.10)` |
| `--glass-shadow` | Drop shadow on panels | `0 8px 32px rgba(0,0,0,0.3)` |
| `--sidebar-bg` | Sidebar background | `rgba(10,10,26,0.95)` |
| `--item-hover-bg` | Item card hover state | `rgba(74,158,255,0.08)` |

### Particles

| Variable | What it affects | Example |
|----------|----------------|---------|
| `--particle-color` | Combine burst particles | `rgba(74,158,255,0.6)` |

### Rarity colours

| Variable | Rarity tier |
|----------|------------|
| `--rarity-common` | Common |
| `--rarity-uncommon` | Uncommon |
| `--rarity-rare` | Rare |
| `--rarity-legendary` | Legendary |
| `--rarity-mythic` | Mythic |
| `--rarity-transcendent` | Transcendent |

---

## Layout & Structure Overrides

These variables let you change the actual shape and layout of the UI — not just colours.

| Variable | What it affects | Default | Example |
|----------|----------------|---------|---------|
| `--card-radius` | Border radius of item cards | `10px` | `0px` (sharp), `20px` (pill) |
| `--btn-radius` | Border radius of all buttons | `8px` | `0px` (rectangular), `30px` (pill) |
| `--sidebar-w` | Width of the right sidebar | `clamp(280px, 25vw, 360px)` | `320px` |
| `--navbar-bg` | Bottom navigation bar background | `rgba(10,10,26,0.97)` | `rgba(0,0,0,0.8)` |
| `--canvas-bg` | Craft canvas background | `transparent` | `rgba(255,255,255,0.02)` |
| `--font-size-base` | Base font size across the UI | `13px` | `14px`, `12px` |
| `--font-weight-label` | Font weight of labels | `500` | `400` (lighter), `700` (bold) |

### Example: Sharp-edged industrial theme

```json
{
  "id": "industrial",
  "name": "Industrial",
  "variables": {
    "--bg": "#0a0a0a",
    "--accent": "#ff4400",
    "--card-radius": "0px",
    "--btn-radius": "2px",
    "--navbar-bg": "rgba(20,0,0,0.98)",
    "--font-size-base": "12px"
  }
}
```

### Example: Rounded bubbly theme

```json
{
  "id": "bubbly",
  "name": "Bubbly",
  "variables": {
    "--bg": "#1a0030",
    "--accent": "#cc66ff",
    "--card-radius": "20px",
    "--btn-radius": "30px",
    "--sidebar-w": "300px"
  }
}
```

---

## Background Animations

Your animation runs on a full-screen `<canvas>` behind the game. The function is called once per frame via `requestAnimationFrame`.

**Arguments:** `ctx`, `W`, `H`, `state`, `accent`

```js
// state persists between frames — initialise things here
if (!state.dots) {
  state.dots = Array.from({ length: 60 }, () => ({
    x: Math.random() * W, y: Math.random() * H,
    vx: (Math.random() - 0.5) * 0.5,
    vy: (Math.random() - 0.5) * 0.5,
  }));
}

for (const d of state.dots) {
  d.x += d.vx; d.y += d.vy;
  if (d.x < 0) d.x = W; if (d.x > W) d.x = 0;
  if (d.y < 0) d.y = H; if (d.y > H) d.y = 0;
  ctx.beginPath();
  ctx.arc(d.x, d.y, 2, 0, Math.PI * 2);
  ctx.fillStyle = accent;
  ctx.fill();
}
```

**Rules:** no `fetch`, no DOM access, no `eval`. Errors are silently swallowed — check the browser console if your animation isn't appearing. Keep particle counts under 150.

---

## Cursor Trails

Cursor trails are built-in effects controlled by the **Settings → Appearance → Cursor Trail** dropdown. They are not part of the theme file format — players pick their trail independently.

### Free trails (everyone gets these)

| Trail | Description |
|-------|-------------|
| None | No trail |
| ✨ Sparkle Dust | Rotating 4-point stars that fade and drift |
| ☄️ Comet Tail | Glowing dots that streak behind the cursor |
| 🫧 Soap Bubbles | Floating outlined circles |
| 🎀 Silk Ribbon | Smooth connected line that fades |

### GitHub star unlocks ⭐

Star the repo at **Settings → Account → Star on GitHub** to unlock:

| Trail | Description |
|-------|-------------|
| 🔥 Fire Trail | Rising heat-hued particles with glow |
| 🌌 Galaxy Swirl | Multi-hued orbiting starfield |

The trail colour always matches the active theme's `--accent` colour, so it automatically fits any theme.

---

## Packaging a Theme Pack (.zip)

```
my-theme.zip
├── theme.json        ← required
└── animation.js      ← optional
```

Both flat and single-folder layouts are accepted.

---

## Importing a Theme

1. Open **Settings → Appearance → Custom Themes**
2. Drop your `.zip` or `.json` onto the drop zone
3. Switch to it via **Settings → Appearance → Theme**

---

## Supporter Packs

Set `"type": "supporter"` in your theme.json — this shows a 💜 badge. Sell the zip on Ko-fi as a digital product. No keys or servers needed.

---

## Full Examples

### Crimson industrial (sharp, minimal)

```json
{
  "id": "crimson-industrial",
  "name": "Crimson Industrial",
  "author": "example",
  "type": "community",
  "variables": {
    "--bg": "#0a0000",
    "--bg-grad": "linear-gradient(135deg, #0a0000, #1a0505)",
    "--accent": "#cc2200",
    "--accent-glow": "rgba(204,34,0,0.4)",
    "--text": "#f0d0d0",
    "--text-dim": "#885555",
    "--glass-bg": "rgba(200,30,0,0.06)",
    "--glass-border": "rgba(200,50,0,0.15)",
    "--card-radius": "2px",
    "--btn-radius": "2px",
    "--navbar-bg": "rgba(10,0,0,0.99)",
    "--font-size-base": "12px",
    "--rarity-legendary": "#ff6600",
    "--rarity-mythic": "#ff0000"
  }
}
```

### Deep ocean (rounded, soft)

```json
{
  "id": "deep-ocean",
  "name": "Deep Ocean",
  "author": "example",
  "type": "supporter",
  "variables": {
    "--bg": "#010d1a",
    "--accent": "#00aaff",
    "--accent-glow": "rgba(0,170,255,0.4)",
    "--text": "#c8e8ff",
    "--text-dim": "#406080",
    "--glass-bg": "rgba(0,100,200,0.07)",
    "--glass-border": "rgba(0,150,255,0.15)",
    "--card-radius": "16px",
    "--btn-radius": "20px",
    "--sidebar-w": "340px",
    "--rarity-rare": "#00ccff",
    "--rarity-legendary": "#0066ff"
  },
  "animation_code": "if(!state.b){state.b=Array.from({length:50},()=>({x:Math.random()*W,y:H+Math.random()*H,r:2+Math.random()*6,v:0.3+Math.random()*0.7,wo:Math.random()*Math.PI*2}));}for(const b of state.b){b.y-=b.v;b.x+=Math.sin(b.wo+b.y*0.01)*0.4;if(b.y<-20){b.y=H+20;b.x=Math.random()*W;}const a=Math.max(0,(H-b.y)/H)*0.4;ctx.beginPath();ctx.arc(b.x,b.y,b.r,0,Math.PI*2);ctx.strokeStyle=`rgba(0,180,255,${a})`;ctx.lineWidth=1;ctx.stroke();}"
}
```

---

## Tips

- **Check all rarities.** A good theme makes all six rarity tiers visually distinct.
- **Test `--card-radius: 0px`.** Sharp corners completely change the feel of a theme.
- **`--bg-grad` overrides `--bg`** for gradient backgrounds — omit it for a flat colour.
- **The cursor trail uses `--accent`** automatically, so your trail always matches the theme.
- **Keep animation files under 5 KB.** Buyers shouldn't download megabytes for a visual effect.
- **Name your `id` permanently.** Changing it breaks existing installations. The `name` can change freely.

---

*Questions or submissions? Open an issue on the Alchemica GitHub.*
