#!/usr/bin/env python3
"""
Builds every image in assets/ for the github.com/TheParthi profile README.

Nothing on the profile comes from a third-party card service. Those render in
their own styles, rate-limit, and go blank when the shared deployment is busy;
these are drawn here, in one palette, and committed to the repo.

  static   hero, about, work, stack, projects, section headers, buttons
  live     activity, languages, footer - from the GitHub GraphQL API

Run nightly by .github/workflows/profile.yml. Locally:  python3 scripts/build.py

Auth: GITHUB_TOKEN when set (the Action); otherwise the `gh` CLI, so a local run
never handles a token. If the API is unreachable the live cards are LEFT AS
THEY ARE rather than overwritten with zeros.

Standard library only - the Action needs no pip install.

THE PAGE GRID
-------------
GitHub lays a profile README out in a ~846px column, and it puts whitespace
between inline images. Rows built from images at 49% / 32% / 24% therefore
never add up to the column, get centred, and sit visibly indented inside the
full-width cards above and below them.

So every image here is drawn on one grid. The page is PAGE units wide; every
SVG carries the same transparent PAD on its left and right; row items are
exact fractions of PAGE. Placed at 100% / 50% / 33.33% / 25% with no
whitespace between the tags, every outer edge lines up and every gutter is
2 * PAD - by construction, not by luck.

PERFORMANCE
-----------
No SVG filters. A soft glow is a radial gradient, which looks the same as a
blurred circle and costs nothing; a Gaussian blur is among the most expensive
things a browser rasterises, and an animated element under one is re-blurred
every frame. Animation is kept to small regions.

And nothing animates forever. An SVG shown through <img> is re-rasterised in
full on every animation frame - the hero costs ~14ms a frame at 2x - so every
animation here runs for about 30 seconds and then settles: the motion is there
when someone lands on the page, and after that the page costs nothing.
Measured, the old hero took ~66ms per frame, every frame, indefinitely.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

LOGIN = "TheParthi"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
SRC = OUT / "src"

# Repos that are someone else's code committed in bulk. Left in, they were 92%
# of every byte counted, so the language bar described Hugging Face and BNB
# Chain rather than the person whose profile it is.
#   transformers    - Hugging Face's library ("Copyright 2020 The HuggingFace
#                     Team"), committed by `root`; 62 MB, 59.8% of all bytes
#   chain_tool_kit  - a BNB Chain toolkit, 22 MB in 3 commits; 31.9%
VENDORED = {"transformers", "chain_tool_kit"}

# ── Grid ─────────────────────────────────────────────────────────────────────
PAGE, PAD = 1224, 12                # divisible by 2, 3 and 4
FULL = PAGE - 2 * PAD               # 1200
HALF = PAGE // 2 - 2 * PAD          # 588
THIRD = PAGE // 3 - 2 * PAD         # 384
QUARTER = PAGE // 4 - 2 * PAD       # 282

SETTLE = 30                         # seconds of motion before everything comes to rest

# ── Tokens ───────────────────────────────────────────────────────────────────
BG0, BG1 = "#0B1120", "#111A33"
TEXT, MUTED, DIM = "#E2E8F0", "#94A3B8", "#64748B"
INDIGO, VIOLET, CYAN = "#6366F1", "#A855F7", "#06B6D4"
PINK, GREEN, AMBER, ROSE, ORANGE = "#F472B6", "#34D399", "#FBBF24", "#FB7185", "#FB923C"
DENDO_BLUE = "#132E7C"

SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"

# Type scale. The column renders PAGE units at ~0.69px, so nothing goes below
# 15 units (~10.5px) and body copy is 20 (~14px).
T_LABEL, T_CHIP, T_BODY, T_SMALL = 15, 16, 20, 15

HEAT = ["#172036", "#312E81", "#4F46E5", "#818CF8", "#67E8F9"]


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def text_width(s: str, size: float, mono=False, bold=False) -> float:
    """SVG cannot measure text, so estimate - generously, because an
    overestimate leaves air and an underestimate clips a word."""
    k = 0.605 if mono else (0.6 if bold else 0.56)
    return len(s) * size * k


def wrap(s: str, limit: int) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        if cur and len(cur) + 1 + len(word) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


def data_uri(name: str) -> str:
    # An SVG shown through <img> is sandboxed and fetches nothing external, so
    # an image must be inlined or it renders as nothing.
    return "data:image/png;base64," + base64.b64encode((SRC / name).read_bytes()).decode()


def write(name: str, svg: str):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(svg, encoding="utf-8")
    print(f"  wrote assets/{name}")


# ── Frame ────────────────────────────────────────────────────────────────────
def card(cw, h, inner, *, label, accent=None, glows=(), grid=True, extra_defs="", rx=18):
    """A dark rounded panel, cw wide, inside the grid's side padding.

    `inner` is drawn in card coordinates (0..cw). Glows are radial gradients:
    (cx, cy, r, colour, peak opacity)."""
    W = cw + 2 * PAD
    defs = [
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BG0}"/>'
        f'<stop offset="0.6" stop-color="{BG1}"/><stop offset="1" stop-color="{BG0}"/></linearGradient>',
        '<pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">'
        '<path d="M32 0H0V32" fill="none" stroke="#FFFFFF" stroke-opacity="0.04"/></pattern>',
        f'<clipPath id="clip"><rect width="{cw}" height="{h}" rx="{rx}"/></clipPath>',
        extra_defs,
    ]
    body = [f'<rect width="{cw}" height="{h}" fill="url(#bg)"/>']
    if grid:
        body.append(f'<rect width="{cw}" height="{h}" fill="url(#grid)"/>')
    for i, (cx, cy, r, c, o) in enumerate(glows):
        defs.append(f'<radialGradient id="glow{i}"><stop offset="0" stop-color="{c}" stop-opacity="{o}"/>'
                    f'<stop offset="1" stop-color="{c}" stop-opacity="0"/></radialGradient>')
        body.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#glow{i})"/>')
    if accent:
        defs.append(f'<linearGradient id="acc" x1="0" y1="0" x2="1" y2="0">'
                    f'<stop offset="0" stop-color="{accent[0]}"/><stop offset="1" stop-color="{accent[1]}"/>'
                    f'</linearGradient>')
        body.append(f'<rect width="{cw}" height="3" fill="url(#acc)"/>')
    body.append(inner)
    body.append(f'<rect x="0.5" y="0.5" width="{cw - 1}" height="{h - 1}" rx="{rx - 0.5}" '
                f'fill="none" stroke="#FFFFFF" stroke-opacity="0.09"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" '
            f'role="img" aria-label="{esc(label)}"><defs>{"".join(defs)}</defs>'
            f'<g transform="translate({PAD},0)"><g clip-path="url(#clip)">{"".join(body)}</g></g></svg>')


def chip(x, y, label, color, size=T_CHIP, h=36):
    w = text_width(label, size, mono=True) + 40
    svg = (f'<g transform="translate({x:.1f},{y:.1f})">'
           f'<rect width="{w:.1f}" height="{h}" rx="{h / 2}" fill="{color}" fill-opacity="0.11" '
           f'stroke="{color}" stroke-opacity="0.42"/>'
           f'<circle cx="17" cy="{h / 2}" r="4.4" fill="{color}"/>'
           f'<text x="29" y="{h / 2 + size * 0.36:.1f}" font-family="{MONO}" font-size="{size}" '
           f'fill="{TEXT}">{esc(label)}</text></g>')
    return svg, w


def flow(items, x0, y0, max_w, *, size=T_CHIP, gap=10, row=46):
    """Chips laid left to right, wrapping at max_w. Returns (svg, bottom)."""
    out, x, y = [], x0, y0
    for label, color in items:
        w = text_width(label, size, mono=True) + 40
        if x > x0 and x + w > x0 + max_w:
            x, y = x0, y + row
        s, w = chip(x, y, label, color, size=size)
        out.append(s)
        x += w + gap
    return "".join(out), y + 36


def arrow(x, y, color=MUTED, s=1.0):
    return (f'<g transform="translate({x},{y}) scale({s})" fill="none" stroke="{color}" stroke-width="2.2" '
            f'stroke-linecap="round" stroke-linejoin="round"><path d="M5 19L19 5"/><path d="M8 5h11v11"/></g>')


def live_pill(x, y, label="LIVE ON GOOGLE PLAY", size=14):
    w = text_width(label, size, mono=True) + 46
    return (f'<g transform="translate({x:.1f},{y})"><rect width="{w:.1f}" height="34" rx="17" fill="{GREEN}" '
            f'fill-opacity="0.12" stroke="{GREEN}" stroke-opacity="0.45"/>'
            f'<circle cx="18" cy="17" r="5" fill="{GREEN}"><animate attributeName="opacity" values="1;0.3;1" '
            f'dur="2.2s" repeatCount="{SETTLE / 2.2:.0f}" fill="freeze"/></circle>'
            f'<text x="32" y="{17 + size * 0.36:.1f}" font-family="{MONO}" font-size="{size}" letter-spacing="1" '
            f'fill="#6EE7B7">{label}</text></g>'), w


# ── Icons (24-unit grid, stroked) ────────────────────────────────────────────
ICONS = {
    "shield": '<path d="M12 3l7 3v6c0 4.6-3 8-7 9-4-1-7-4.4-7-9V6l7-3z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 7.5l8.5 6 8.5-6"/>',
    "link": '<path d="M10 14a4.2 4.2 0 0 0 6 0l3-3a4.2 4.2 0 0 0-6-6l-1.2 1.2"/><path d="M14 10a4.2 4.2 0 0 0-6 0l-3 3a4.2 4.2 0 0 0 6 6l1.2-1.2"/>',
    "cloud": '<path d="M7 18.5h10.2a4.2 4.2 0 0 0 .6-8.4A6.2 6.2 0 0 0 6 9.6a4.5 4.5 0 0 0 1 8.9z"/><path d="M12 15.5v-5M9.8 12.6L12 10.4l2.2 2.2"/>',
    "trend": '<path d="M3 17.5l6-6 4 4 8-8"/><path d="M14.5 7.5H21v6.5"/>',
    "brief": '<rect x="3" y="7.5" width="18" height="12.5" rx="2.5"/><path d="M8.5 7.5V5.8A1.8 1.8 0 0 1 10.3 4h3.4a1.8 1.8 0 0 1 1.8 1.8v1.7"/><path d="M3 13h18"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.6 2.7 3.9 5.7 3.9 9s-1.3 6.3-3.9 9c-2.6-2.7-3.9-5.7-3.9-9s1.3-6.3 3.9-9z"/>',
    "phone": '<rect x="7" y="2.5" width="10" height="19" rx="2.6"/><path d="M10.5 18.3h3"/>',
    "pin": '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.4"/>',
    "grid": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="4.5" rx="1.6"/>'
            '<rect x="13.5" y="11" width="7" height="9.5" rx="1.6"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>',
    "server": '<rect x="3.5" y="4" width="17" height="6.5" rx="2"/><rect x="3.5" y="13.5" width="17" height="6.5" rx="2"/>'
              '<path d="M7.5 7.25h.01M7.5 16.75h.01"/>',
    "branch": '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><circle cx="6" cy="18" r="2.5"/>'
              '<path d="M6 8.5v7M18 15.5V12a3 3 0 0 0-3-3H9"/>',
}


def icon(name, cx, cy, color, scale=1.15, sw=1.8):
    if name == "in":
        return (f'<text x="{cx}" y="{cy + 8}" text-anchor="middle" font-family="{SANS}" font-size="24" '
                f'font-weight="800" fill="{color}">in</text>')
    return (f'<g transform="translate({cx - 12 * scale:.1f},{cy - 12 * scale:.1f}) scale({scale})" fill="none" '
            f'stroke="{color}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>')


def card_header(title, note, cw, pad=40):
    return (f'<text x="{pad}" y="56" font-family="{SANS}" font-size="26" font-weight="750" fill="{TEXT}">{esc(title)}</text>'
            f'<text x="{cw - pad}" y="56" text-anchor="end" font-family="{MONO}" font-size="{T_SMALL}" fill="{DIM}">{esc(note)}</text>'
            f'<rect x="{pad}" y="78" width="{cw - 2 * pad}" height="1" fill="#FFFFFF" fill-opacity="0.07"/>')


# ════════════════════════════════════════════════════════════════════════════
#  HERO
# ════════════════════════════════════════════════════════════════════════════
PHRASES = ["ship --react-native --nestjs --postgis",
           "build --ai-agents --rag --langgraph",
           "secure --genai --threat-detection"]


def build_hero():
    cw, h = FULL, 360
    fs, tx = 21, 66
    cwid = 0.605 * fs
    widths = [len("$ " + p) * cwid + 6 for p in PHRASES]
    # 15s loop, three 5s slots: each phrase types in over 1.6s, holds, then clears.
    kt, wv, cx = [], [], []
    for i, w in enumerate(widths):
        a = i / 3
        kt += [a, a + 0.11, a + 0.32]
        wv += [0, w, w]
        cx += [tx + 4, tx + 4 + w, tx + 4 + w]
    kt.append(1.0)
    wv.append(0)
    cx.append(tx + 4)
    kts = ";".join(f"{k:.3f}" for k in kt)
    texts = []
    for i, p in enumerate(PHRASES):
        a, b = i / 3, (i + 1) / 3
        if i == 0:
            ov, okt = "1;1;0;0", f"0;{b - 0.002:.3f};{b:.3f};1"
        else:
            ov, okt = "0;0;1;1;0;0", f"0;{a:.3f};{a + 0.001:.3f};{b - 0.002:.3f};{b:.3f};1"
        texts.append(
            f'<text x="{tx}" y="290" font-family="{MONO}" font-size="{fs}" fill="#A5B4FC" opacity="{1 if i == 0 else 0}">'
            f'<tspan fill="#64748B">$ </tspan>{esc(p)}'
            f'<animate attributeName="opacity" values="{ov}" keyTimes="{okt}" dur="15s" repeatCount="2"/></text>')

    defs = (
        f'<linearGradient id="nameFill" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#FFFFFF"/>'
        f'<stop offset="0.55" stop-color="#C7D2FE"/><stop offset="1" stop-color="#67E8F9"/></linearGradient>'
        f'<linearGradient id="rule" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{INDIGO}"/>'
        f'<stop offset="0.5" stop-color="{VIOLET}"/><stop offset="1" stop-color="{CYAN}"/></linearGradient>'
        # Static width = the first phrase fully typed, so a renderer that does
        # not animate still shows a complete line.
        f'<clipPath id="typeClip"><rect x="{tx}" y="262" width="{widths[0]:.0f}" height="40">'
        f'<animate attributeName="width" values="{";".join(f"{v:.0f}" for v in wv)}" keyTimes="{kts}" '
        f'dur="15s" repeatCount="2"/></rect></clipPath>')
    body = [
        f'<g transform="translate(66,48)"><rect width="230" height="36" rx="18" fill="{GREEN}" fill-opacity="0.12" '
        f'stroke="{GREEN}" stroke-opacity="0.45"/><circle cx="20" cy="18" r="5.2" fill="#34D399">'
        f'<animate attributeName="opacity" values="1;0.25;1" dur="2.2s" repeatCount="{SETTLE / 2.2:.0f}" fill="freeze"/></circle>'
        f'<text x="36" y="23.4" font-family="{MONO}" font-size="15" letter-spacing="1.2" fill="#6EE7B7">OPEN TO SDE ROLES</text></g>',
        f'<text x="62" y="160" font-family="{SANS}" font-size="58" font-weight="800" letter-spacing="-1.2" '
        f'fill="url(#nameFill)">Parthiban Gunasekaran</text>',
        f'<rect x="66" y="186" width="140" height="4" rx="2" fill="url(#rule)"/>',
        f'<text x="66" y="228" font-family="{SANS}" font-size="22" font-weight="650" letter-spacing="4" '
        f'fill="#94A3B8">FULL STACK DEVELOPER</text>',
        f'<g clip-path="url(#typeClip)">{"".join(texts)}</g>',
        f'<rect x="{tx + 4 + widths[0]:.0f}" y="268" width="2.6" height="27" fill="#67E8F9">'
        f'<animate attributeName="x" values="{";".join(f"{v:.0f}" for v in cx)}" keyTimes="{kts}" '
        f'dur="15s" repeatCount="2"/>'
        f'<animate attributeName="opacity" values="0;0;1;1" keyTimes="0;0.5;0.5;1" dur="1.1s" '
        f'repeatCount="{SETTLE / 1.1:.0f}" fill="freeze"/></rect>',
        # Orbit - small, so animating it is cheap.
        '<g transform="translate(1010,180)">'
        '<circle r="100" fill="none" stroke="#64748B" stroke-opacity="0.7"/>'
        '<circle r="68" fill="none" stroke="#64748B" stroke-opacity="0.55"/>'
        '<circle r="36" fill="none" stroke="#64748B" stroke-opacity="0.4"/>'
        '<circle r="14" fill="#818CF8"/>'
        '<g><circle cx="100" r="7.5" fill="#67E8F9"/><animateTransform attributeName="transform" type="rotate" '
        'from="0" to="360" dur="14s" repeatCount="2" fill="freeze"/></g>'
        '<g><circle cx="68" r="6.5" fill="#A855F7"/><animateTransform attributeName="transform" type="rotate" '
        'from="140" to="500" dur="9s" repeatCount="3" fill="freeze"/></g>'
        '<g><circle cx="36" r="5" fill="#F472B6"/><animateTransform attributeName="transform" type="rotate" '
        'from="300" to="-60" dur="6.5s" repeatCount="4" fill="freeze"/></g></g>',
    ]
    write("hero.svg", card(cw, h, "".join(body), label="Parthiban Gunasekaran, Full Stack Developer. Open to SDE roles.",
                           extra_defs=defs, rx=22,
                           glows=((260, 70, 380, INDIGO, 0.42), (990, 300, 400, CYAN, 0.30),
                                  (660, -10, 340, VIOLET, 0.32))))


# ════════════════════════════════════════════════════════════════════════════
#  ABOUT - an editor window
# ════════════════════════════════════════════════════════════════════════════
CODE = [
    '/** Ships AI-powered, security-first products — end to end. */',
    '@Profile(focus = { "Mobility", "AI", "Security" })',
    'public final class Parthiban extends FullStackEngineer {',
    '',
    '    String   building   = "NXA Ride @ Dendo";',
    '    String[] languages  = { "Java", "TypeScript", "Python", "SQL" };',
    '    String[] backend    = { "Spring Boot", "NestJS", "Socket.IO" };',
    '    String[] mobile     = { "React Native", "React", "Next.js" };',
    '    String[] ai         = { "GenAI", "LangChain", "LangGraph", "RAG" };',
    '    String[] cloud      = { "AWS", "Azure", "Docker", "GitHub Actions" };',
    '',
    '    boolean  openToWork = true;   // SDE · Full-Stack roles',
    '}',
]

SYNTAX = re.compile(
    r'(?P<cm>/\*\*.*?\*/|//.*$)|(?P<st>"[^"]*")|(?P<an>@\w+)'
    r'|(?P<bo>\b(?:true|false|null)\b|\b\d+\b)'
    r'|(?P<kw>\b(?:public|private|final|static|class|extends|implements|boolean|return|new)\b)'
    r'|(?P<ty>\b[A-Z]\w*(?:\[\])?)|(?P<fd>\b[a-z]\w*(?=\s*=))'
    r'|(?P<pu>[^\w\s"@/]+|/)|(?P<sp>\s+)|(?P<tx>\w+)')
TOKEN_COLOR = {"cm": "#64748B", "st": "#86EFAC", "an": "#FBBF24", "bo": "#F472B6", "kw": "#C084FC",
               "ty": "#67E8F9", "fd": "#93C5FD", "pu": "#94A3B8", "sp": None, "tx": TEXT}


def highlight(line: str) -> str:
    out = []
    for m in SYNTAX.finditer(line):
        kind, tok = m.lastgroup, esc(m.group())
        color = TOKEN_COLOR[kind]
        if color is None:
            out.append(tok)
        elif kind == "cm":
            out.append(f'<tspan fill="{color}" font-style="italic">{tok}</tspan>')
        else:
            out.append(f'<tspan fill="{color}">{tok}</tspan>')
    return "".join(out)


def build_about():
    cw, top, lh, fs = FULL, 56, 35, 20
    first, status_h = top + 50, 44
    h = first + (len(CODE) - 1) * lh + 34 + status_h
    gx, codex = 62, 96
    hl = len(CODE) - 2
    p = [f'<rect width="{cw}" height="{top}" fill="#FFFFFF" fill-opacity="0.025"/>',
         f'<rect y="{top - 1}" width="{cw}" height="1" fill="#FFFFFF" fill-opacity="0.07"/>']
    for i, c in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        p.append(f'<circle cx="{28 + i * 22}" cy="{top / 2}" r="7" fill="{c}"/>')
    p += [
        f'<rect x="104" y="10" width="226" height="{top - 10}" rx="9" fill="{BG0}"/>',
        f'<rect x="104" y="{top - 2}" width="226" height="2" fill="url(#tabline)"/>',
        f'<circle cx="126" cy="{top / 2 + 5}" r="5" fill="{ORANGE}"/>',
        f'<text x="140" y="{top / 2 + 10.5}" font-family="{MONO}" font-size="16" fill="{TEXT}">Parthiban.java</text>',
        f'<text x="352" y="{top / 2 + 10.5}" font-family="{MONO}" font-size="16" fill="{DIM}">Projects.java</text>',
        f'<text x="{cw - 26}" y="{top / 2 + 6}" text-anchor="end" font-family="{MONO}" font-size="{T_SMALL}" '
        f'fill="{DIM}" xml:space="preserve">UTF-8  ·  LF  ·  Java 21</text>',
        f'<rect y="{first + hl * lh - lh * 0.72:.1f}" width="{cw}" height="{lh}" fill="{INDIGO}" fill-opacity="0.10"/>',
        f'<rect y="{first + hl * lh - lh * 0.72:.1f}" width="3" height="{lh}" fill="{INDIGO}"/>',
    ]
    for i, line in enumerate(CODE):
        y = first + i * lh
        p.append(f'<text x="{gx}" y="{y}" text-anchor="end" font-family="{MONO}" font-size="{fs - 3}" '
                 f'fill="{TEXT if i == hl else "#3B4A63"}">{i + 1}</text>')
        if line:
            p.append(f'<text x="{codex}" y="{y}" font-family="{MONO}" font-size="{fs}" xml:space="preserve" '
                     f'fill="{TEXT}">{highlight(line)}</text>')
    cy = first + (len(CODE) - 1) * lh
    p.append(f'<rect x="{codex + 15}" y="{cy - 19}" width="2.6" height="25" fill="#67E8F9">'
             f'<animate attributeName="opacity" values="0;0;1;1" keyTimes="0;0.5;0.5;1" dur="1.1s" '
             f'repeatCount="{SETTLE / 1.1:.0f}" fill="freeze"/></rect>')
    mx = cw - 124
    p.append(f'<rect x="{mx - 10}" y="{top + 14}" width="108" height="{len(CODE) * 8 + 14}" rx="5" '
             f'fill="#FFFFFF" fill-opacity="0.035" stroke="#FFFFFF" stroke-opacity="0.06"/>')
    for i, line in enumerate(CODE):
        if line.strip():
            ind = (len(line) - len(line.lstrip())) * 1.1
            bw = min(len(line.strip()) * 1.2, 90 - ind)
            col = "#64748B" if line.strip().startswith("/") else (INDIGO if i == hl else "#475569")
            p.append(f'<rect x="{mx + ind:.1f}" y="{top + 21 + i * 8}" width="{bw:.1f}" height="3.6" rx="1.8" '
                     f'fill="{col}" fill-opacity="0.85"/>')
    sy = h - status_h
    st = sy + status_h / 2 + 5.4
    p += [
        f'<rect y="{sy}" width="{cw}" height="{status_h}" fill="url(#status)"/>',
        icon("branch", 30, sy + status_h / 2, "#FFFFFF", scale=0.7, sw=2.2),
        f'<text x="48" y="{st}" font-family="{MONO}" font-size="{T_SMALL}" fill="#FFFFFF">main</text>',
        f'<text x="112" y="{st}" font-family="{MONO}" font-size="{T_SMALL}" fill="#FFFFFF" fill-opacity="0.85">✓ 0 problems</text>',
        f'<text x="{cw - 26}" y="{st}" text-anchor="end" font-family="{MONO}" font-size="{T_SMALL}" fill="#FFFFFF" '
        f'fill-opacity="0.9" xml:space="preserve">Ln {len(CODE)}, Col 2    Spaces: 4    Java</text>',
    ]
    defs = (f'<linearGradient id="tabline" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{INDIGO}"/>'
            f'<stop offset="1" stop-color="{CYAN}"/></linearGradient>'
            f'<linearGradient id="status" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#4F46E5"/>'
            f'<stop offset="0.55" stop-color="#7C3AED"/><stop offset="1" stop-color="#0891B2"/></linearGradient>')
    write("about.svg", card(cw, h, "".join(p), label="About Parthiban, written as a Java class", grid=False,
                            extra_defs=defs, rx=16,
                            glows=((1060, 140, 340, VIOLET, 0.20), (150, h - 40, 300, INDIGO, 0.16))))


# ════════════════════════════════════════════════════════════════════════════
#  WORK - NXA Ride and Dendo
# ════════════════════════════════════════════════════════════════════════════
# The route the vehicle drives. Pickup and drop are Madiwala and Silk Board -
# the pair NXA Ride is tested against every day.
ROUTE = "M70 318 L70 212 Q70 196 86 196 L304 196 Q320 196 320 180 L320 102 Q320 86 336 86 L396 86"


def map_panel(mx, my, mw, mh):
    dy = (mh - 380) / 2                     # centre the 440x380 scene vertically
    roads = []
    for y in (86, 196, 286):
        roads.append(f'<path d="M-10 {y + dy:.0f}H{mw + 10}" stroke="#1A2540" stroke-width="9"/>')
    for x in (70, 190, 320, 400):
        roads.append(f'<path d="M{x} -10V{mh + 10}" stroke="#1A2540" stroke-width="9"/>')
    roads.append(f'<path d="M-30 {350 + dy:.0f}L{mw + 30} {40 + dy:.0f}" stroke="#25335A" stroke-width="15"/>')
    roads.append(f'<path d="M-30 {350 + dy:.0f}L{mw + 30} {40 + dy:.0f}" stroke="#33446F" stroke-width="1.3" '
                 f'stroke-dasharray="10 12"/>')

    def label(x, y, w, dot, txt):
        return (f'<g transform="translate({x},{y})"><rect width="{w}" height="34" rx="9" fill="#0B1120" '
                f'fill-opacity="0.94" stroke="#FFFFFF" stroke-opacity="0.13"/><text x="13" y="22.4" '
                f'font-family="{MONO}" font-size="15" fill="{TEXT}"><tspan fill="{dot}">●</tspan> {txt}</text></g>')

    scene = [
        f'<path d="M0 300 C40 262 128 268 160 306 C184 336 150 {mh} 84 {mh} L0 {mh}Z" fill="#0B2438"/>',
        '<rect x="210" y="216" width="92" height="52" rx="10" fill="#0E2620"/>',
        '<rect x="92" y="104" width="78" height="72" rx="10" fill="#0E2620"/>',
        # A wide faint stroke under the line stands in for a glow - no filter.
        f'<path d="{ROUTE}" fill="none" stroke="{INDIGO}" stroke-opacity="0.18" stroke-width="20" '
        f'stroke-linecap="round" stroke-linejoin="round"/>',
        f'<path d="{ROUTE}" fill="none" stroke="{INDIGO}" stroke-opacity="0.30" stroke-width="11" '
        f'stroke-linecap="round" stroke-linejoin="round"/>',
        f'<path d="{ROUTE}" fill="none" stroke="url(#routeG)" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>',
        f'<path d="{ROUTE}" fill="none" stroke="#FFFFFF" stroke-opacity="0.75" stroke-width="2.2" stroke-linecap="round" '
        f'stroke-dasharray="1 13"><animate attributeName="stroke-dashoffset" from="0" to="-28" dur="0.9s" '
        f'repeatCount="{SETTLE / 0.9:.0f}" fill="freeze"/></path>',
        '<circle cx="70" cy="318" r="20" fill="#22C55E" fill-opacity="0.18"/>',
        '<circle cx="70" cy="318" r="10" fill="#22C55E" stroke="#FFFFFF" stroke-width="3"/>',
        label(94, 301, 196, "#4ADE80", "Pickup · Madiwala"),
        '<g transform="translate(396,86)"><path d="M0 0c-8-9-13-14-13-21a13 13 0 0 1 26 0C13-14 8-9 0 0z" fill="#F43F5E" '
        'stroke="#FFFFFF" stroke-width="2.4"/><circle cy="-21" r="4.8" fill="#FFFFFF"/></g>',
        label(176, 30, 210, "#FB7185", "Drop · Silk Board"),
        # Hidden unless the renderer animates, so a static render never shows
        # the vehicle parked at 0,0.
        f'<g opacity="0"><set attributeName="opacity" to="1" begin="0s"/>'
        f'<animateMotion path="{ROUTE}" rotate="auto" dur="9s" repeatCount="3" fill="freeze" keyPoints="0;1;1" '
        f'keyTimes="0;0.86;1" calcMode="linear"/>'
        f'<circle r="20" fill="#67E8F9" fill-opacity="0.18"/>'
        f'<path d="M12 0L-10 9.5L-5 0L-10 -9.5Z" fill="#67E8F9" stroke="#FFFFFF" stroke-width="2.2" '
        f'stroke-linejoin="round"/></g>',
    ]
    g = [
        f'<g transform="translate({mx},{my})" clip-path="url(#mapclip)">',
        f'<rect width="{mw}" height="{mh}" fill="#0C1426"/>',
        f'<g fill="none" stroke-linecap="round">{"".join(roads)}</g>',
        f'<g transform="translate(0,{dy:.0f})">{"".join(scene)}</g>',
        f'<g transform="translate(18,18)"><rect width="196" height="70" rx="15" fill="#0B1120" fill-opacity="0.95" '
        f'stroke="#FFFFFF" stroke-opacity="0.13"/>'
        f'<text x="18" y="29" font-family="{MONO}" font-size="13.5" letter-spacing="1.6" fill="{DIM}">ARRIVING IN</text>'
        f'<text x="18" y="56" font-family="{SANS}" font-size="24" font-weight="800" fill="{TEXT}">6 min'
        f'<tspan dx="8" font-size="16" font-weight="500" fill="{MUTED}">· 2.4 km</tspan></text></g>',
        f'<g transform="translate({mw - 34},{mh - 34})"><circle r="22" fill="#0B1120" stroke="#FFFFFF" stroke-opacity="0.15"/>'
        f'<circle r="6.5" fill="none" stroke="{TEXT}" stroke-width="2.2"/>'
        f'<path d="M0 -13v4.5M0 8.5v4.5M-13 0h4.5M8.5 0h4.5" stroke="{TEXT}" stroke-width="2.2" stroke-linecap="round"/></g>',
        f'<rect width="{mw}" height="{mh}" fill="url(#vig)"/>',
        '</g>',
        f'<rect x="{mx + 0.5}" y="{my + 0.5}" width="{mw - 1}" height="{mh - 1}" rx="18" fill="none" '
        f'stroke="#FFFFFF" stroke-opacity="0.13"/>',
    ]
    defs = (f'<clipPath id="mapclip"><rect width="{mw}" height="{mh}" rx="18"/></clipPath>'
            f'<linearGradient id="routeG" gradientUnits="userSpaceOnUse" x1="70" y1="318" x2="396" y2="86">'
            f'<stop offset="0" stop-color="#22C55E"/><stop offset="0.45" stop-color="{INDIGO}"/>'
            f'<stop offset="1" stop-color="{CYAN}"/></linearGradient>'
            f'<radialGradient id="vig" cx="50%" cy="45%" r="75%"><stop offset="0.55" stop-color="#0B1120" '
            f'stop-opacity="0"/><stop offset="1" stop-color="#0B1120" stop-opacity="0.7"/></radialGradient>')
    return "".join(g), defs


def build_work_nxaride():
    cw, h = FULL, 540
    lw = 660
    p = [
        '<clipPath id="logoclip"><rect x="44" y="44" width="80" height="80" rx="20"/></clipPath>',
        f'<image href="{data_uri("nxa_logo.png")}" x="44" y="44" width="80" height="80" clip-path="url(#logoclip)" '
        f'preserveAspectRatio="xMidYMid slice"/>',
        '<rect x="44.5" y="44.5" width="79" height="79" rx="19.5" fill="none" stroke="#FFFFFF" stroke-opacity="0.18"/>',
        f'<text x="146" y="70" font-family="{MONO}" font-size="{T_LABEL}" letter-spacing="2.2" font-weight="700" '
        f'fill="#60A5FA">PRODUCTION · BUILT AT DENDO</text>',
        f'<text x="144" y="116" font-family="{SANS}" font-size="46" font-weight="800" letter-spacing="-1" fill="{TEXT}">NXA Ride</text>',
    ]
    pill, _ = live_pill(146 + text_width("NXA Ride", 46, bold=True) + 18, 88)
    p.append(pill)
    desc = ("Ride-hailing and parcel delivery: rider app, driver app, admin console and backend "
            "— live booking, real-time tracking over WebSockets and UPI payments.")
    for i, line in enumerate(wrap(desc, 58)):
        p.append(f'<text x="44" y="{176 + i * 30}" font-family="{SANS}" font-size="{T_BODY}" fill="{MUTED}">{esc(line)}</text>')
    tiles = [("phone", "Rider app", "React Native", INDIGO), ("pin", "Driver app", "React Native", CYAN),
             ("grid", "Admin console", "Next.js", VIOLET), ("server", "Backend API", "NestJS", PINK)]
    tw_, th = (lw - 14) / 2, 72
    for i, (ic, title, sub, col) in enumerate(tiles):
        x = 44 + (i % 2) * (tw_ + 14)
        y = 260 + (i // 2) * (th + 12)
        p += [
            f'<rect x="{x:.1f}" y="{y}" width="{tw_:.1f}" height="{th}" rx="15" fill="#FFFFFF" fill-opacity="0.035" '
            f'stroke="#FFFFFF" stroke-opacity="0.08"/>',
            f'<rect x="{x + 14:.1f}" y="{y + 14}" width="44" height="44" rx="12" fill="{col}" fill-opacity="0.14"/>',
            icon(ic, x + 36, y + 36, col, scale=0.95, sw=2),
            f'<text x="{x + 74:.1f}" y="{y + 32}" font-family="{SANS}" font-size="19" font-weight="700" fill="{TEXT}">{title}</text>',
            f'<text x="{x + 74:.1f}" y="{y + 54}" font-family="{MONO}" font-size="{T_SMALL}" fill="{DIM}">{sub}</text>',
        ]
    chips, _ = flow([("React Native", CYAN), ("NestJS", PINK), ("PostGIS", GREEN), ("Redis", ROSE),
                     ("Socket.IO", VIOLET), ("Google Maps", AMBER), ("AWS · Azure", ORANGE)], 44, 444, lw)
    p.append(chips)
    m, mdefs = map_panel(lw + 80, 34, cw - lw - 80 - 34, h - 68)
    p.append(m)
    write("work-nxaride.svg", card(cw, h, "".join(p), label="NXA Ride - ride-hailing and parcel delivery, built at Dendo",
                                   accent=("#22C55E", CYAN), extra_defs=mdefs,
                                   glows=((900, 40, 420, INDIGO, 0.26), (100, h, 380, CYAN, 0.14))))


def build_work_dendo():
    cw, h = FULL, 196
    p = [
        f'<rect x="38" y="38" width="120" height="120" rx="28" fill="{DENDO_BLUE}"/>',
        '<rect x="38.5" y="38.5" width="119" height="119" rx="27.5" fill="none" stroke="#FFFFFF" stroke-opacity="0.2"/>',
        f'<image href="{data_uri("dendo_mark.png")}" x="52" y="52" width="92" height="92"/>',
        f'<text x="186" y="74" font-family="{MONO}" font-size="{T_LABEL}" letter-spacing="2.2" font-weight="700" '
        f'fill="#7C9CF0">THE COMPANY</text>',
        f'<text x="184" y="122" font-family="{SANS}" font-size="42" font-weight="800" letter-spacing="-0.8" fill="{TEXT}">Dendo</text>',
        f'<text x="186" y="158" font-family="{SANS}" font-size="{T_BODY}" fill="{MUTED}">'
        f'Food, groceries and parcels, delivered daily. NXA Ride is a Dendo product.</text>',
    ]
    pw = text_width("LIVE ON GOOGLE PLAY", 14, mono=True) + 46
    pill, _ = live_pill(cw - 40 - 40 - pw, 50)
    p += [pill, arrow(cw - 64, 54)]
    write("work-dendo.svg", card(cw, h, "".join(p), label="Dendo - food, groceries and parcels, delivered daily",
                                 accent=("#3B5BDB", "#7C9CF0"),
                                 glows=((98, 98, 230, "#3B5BDB", 0.45), (1100, 60, 300, INDIGO, 0.16))))


STORES = [("nxaride", "nxa_logo.png", None, "NXA Ride", "RIDER APP"),
          ("nxadriver", "nxa_logo.png", None, "NXA Ride Partner", "DRIVER APP"),
          ("dendo", "dendo_mark.png", DENDO_BLUE, "Dendo", "DELIVERY")]


def build_store_buttons():
    cw, h = THIRD, 92
    for slug, logo, tile, name, sub in STORES:
        if tile:
            p = [f'<rect x="16" y="16" width="60" height="60" rx="16" fill="{tile}"/>',
                 f'<image href="{data_uri(logo)}" x="22" y="22" width="48" height="48"/>']
        else:
            p = ['<clipPath id="lc"><rect x="16" y="16" width="60" height="60" rx="16"/></clipPath>',
                 f'<image href="{data_uri(logo)}" x="16" y="16" width="60" height="60" clip-path="url(#lc)" '
                 f'preserveAspectRatio="xMidYMid slice"/>']
        size = 22 if text_width(name, 22, bold=True) < cw - 96 - 56 else 19
        p += [
            f'<text x="94" y="38" font-family="{MONO}" font-size="13.5" letter-spacing="1.4" fill="{DIM}">'
            f'GOOGLE PLAY · {esc(sub)}</text>',
            f'<text x="94" y="67" font-family="{SANS}" font-size="{size}" font-weight="800" fill="{TEXT}">{esc(name)}</text>',
            f'<path d="M{cw - 44} 34L{cw - 24} 46L{cw - 44} 58Z" fill="none" stroke="{MUTED}" stroke-width="2.2" '
            f'stroke-linejoin="round"/>',
        ]
        write(f"store-{slug}.svg", card(cw, h, "".join(p), label=f"{name} on Google Play", grid=False, rx=18,
                                        glows=((46, 46, 110, tile or INDIGO, 0.5),)))


# ════════════════════════════════════════════════════════════════════════════
#  STACK
# ════════════════════════════════════════════════════════════════════════════
STACK = [
    ("LANGUAGES", INDIGO, ["Java", "TypeScript", "Python", "SQL"]),
    ("BACKEND", VIOLET, ["Spring Boot", "NestJS", "Prisma", "Socket.IO", "REST · JWT"]),
    ("MOBILE · WEB", CYAN, ["React Native", "React", "Next.js", "Tailwind CSS"]),
    ("AI · LLM", PINK, ["GenAI", "LangChain", "LangGraph", "RAG", "Genkit", "ChromaDB"]),
    ("DATA", GREEN, ["PostgreSQL", "PostGIS", "Redis", "MySQL", "Supabase"]),
    ("CLOUD · DEVOPS", AMBER, ["AWS", "Azure", "Docker", "GitHub Actions", "nginx"]),
]


def build_stack():
    cw, pad, cols, gap = FULL, 40, 3, 32
    cell = (cw - 2 * pad - (cols - 1) * gap) / cols
    blocks = []
    for title, color, items in STACK:
        svg, bottom = flow([(it, color) for it in items], 0, 0, cell)
        blocks.append((title, color, svg, bottom))
    p, y = [], 44
    for r in [blocks[i:i + cols] for i in range(0, len(blocks), cols)]:
        for j, (title, color, svg, _) in enumerate(r):
            x = pad + j * (cell + gap)
            p += [f'<rect x="{x:.1f}" y="{y}" width="4" height="18" rx="2" fill="{color}"/>',
                  f'<text x="{x + 14:.1f}" y="{y + 15}" font-family="{MONO}" font-size="{T_LABEL}" letter-spacing="2.2" '
                  f'font-weight="700" fill="{color}">{esc(title)}</text>',
                  f'<g transform="translate({x:.1f},{y + 34})">{svg}</g>']
        y += 34 + max(b[3] for b in r) + 40
    h = y - 8
    write("stack.svg", card(cw, h, "".join(p), label="Tech stack",
                            glows=((1150, 0, 340, CYAN, 0.16), (60, h, 340, VIOLET, 0.14))))


# ════════════════════════════════════════════════════════════════════════════
#  PROJECTS
# ════════════════════════════════════════════════════════════════════════════
PROJECTS = [
    dict(slug="malware", repo="Malware-Detection-GenAI", title="Malware Detection · GenAI",
         kicker="GENAI · SECURITY", icon="shield", color=ROSE, lang=("Python", "#3572A5"),
         desc="Static analysis of uploaded files, Malware/Benign classification with GenAI, "
              "and automated incident response.",
         chips=["Python", "GenAI", "Static analysis"]),
    dict(slug="email-rag", repo="Email_rag", title="AI Support Email Agents",
         kicker="AGENTS · RAG", icon="mail", color=VIOLET, lang=("Python", "#3572A5"),
         desc="LangGraph agents that categorise support email and answer it with RAG over the Gmail API.",
         chips=["LangGraph", "ChromaDB", "Gemini"]),
    dict(slug="loanify", repo="Loanify", title="Loanify",
         kicker="AI · FINTECH", icon="trend", color=AMBER, lang=("TypeScript", "#3178C6"),
         desc="AI-driven NBFC loan automation with customer and admin interfaces and instant eligibility checks.",
         chips=["Next.js", "Genkit", "Firebase"]),
    dict(slug="security-monitor", repo="Security_monitor", title="Blockchain Security Monitor",
         kicker="WEB3 · LLM RISK", icon="link", color=CYAN, lang=("Python", "#3572A5"),
         desc="Flags high-risk ERC-20 allowances and explains the risk with LLMs (DeepSeek, Gemini, OpenAI).",
         chips=["Python", "ERC-20", "LLMs"]),
    dict(slug="trustlance", repo="Freelancer_Marketplace_TrustLance", title="TrustLance",
         kicker="MARKETPLACE · FULL STACK", icon="brief", color=GREEN, lang=("TypeScript", "#3178C6"),
         desc="Freelance marketplace with role-based access and authenticated APIs, on Next.js with "
              "Supabase and Firebase.",
         chips=["Next.js", "Supabase", "Tailwind"]),
    dict(slug="cloud-backup", repo="secure-cloud-storage-backup-system", title="Secure Cloud Backup",
         kicker="CLOUD · DATA PROTECTION", icon="cloud", color=ORANGE, lang=("AWS", ORANGE),
         desc="Data-protection-first storage and backup on AWS, built around S3 and EC2 for availability "
              "and recovery.",
         chips=["AWS", "S3", "EC2"]),
]


def build_project(pr):
    cw, h, pad = HALF, 372, 34
    c = pr["color"]
    tsize = min(30, (cw - pad - 96 - 40) / (len(pr["title"]) * 0.6))
    p = [
        f'<rect x="{pad}" y="{pad}" width="64" height="64" rx="17" fill="{c}" fill-opacity="0.13" stroke="{c}" '
        f'stroke-opacity="0.45"/>',
        icon(pr["icon"], pad + 32, pad + 32, c, scale=1.25, sw=1.9),
        f'<text x="{pad + 84}" y="{pad + 24}" font-family="{MONO}" font-size="{T_LABEL - 0.5}" letter-spacing="1.8" '
        f'font-weight="700" fill="{c}">{esc(pr["kicker"])}</text>',
        f'<text x="{pad + 84}" y="{pad + 58}" font-family="{SANS}" font-size="{tsize:.1f}" font-weight="750" '
        f'fill="{TEXT}">{esc(pr["title"])}</text>',
        arrow(cw - pad - 24, pad + 2),
    ]
    for i, line in enumerate(wrap(pr["desc"], 48)[:3]):
        p.append(f'<text x="{pad}" y="{pad + 124 + i * 29}" font-family="{SANS}" font-size="{T_BODY - 1}" '
                 f'fill="{MUTED}">{esc(line)}</text>')
    chips, _ = flow([(it, c) for it in pr["chips"]], pad, 248, cw - 2 * pad, size=15)
    p.append(chips)
    repo = pr["repo"]
    lname, lcolor = pr["lang"]
    lw = text_width(lname, T_SMALL, mono=True)
    room = (cw - 2 * pad - lw - 40) / (T_SMALL * 0.605)
    if len(repo) > room:
        repo = repo[:int(room) - 1] + "…"
    p += [
        f'<rect x="{pad}" y="{h - 62}" width="{cw - 2 * pad}" height="1" fill="#FFFFFF" fill-opacity="0.08"/>',
        f'<text x="{pad}" y="{h - 26}" font-family="{MONO}" font-size="{T_SMALL}" fill="{DIM}">{esc(repo)}</text>',
        f'<circle cx="{cw - pad - lw - 15:.1f}" cy="{h - 31}" r="5.5" fill="{lcolor}"/>',
        f'<text x="{cw - pad}" y="{h - 26}" text-anchor="end" font-family="{MONO}" font-size="{T_SMALL}" '
        f'fill="{MUTED}">{esc(lname)}</text>',
    ]
    write(f"project-{pr['slug']}.svg", card(cw, h, "".join(p), label=f"{pr['title']} - {pr['desc']}",
                                            accent=(c, INDIGO), glows=((cw - 40, 30, 240, c, 0.26),)))


# ════════════════════════════════════════════════════════════════════════════
#  SECTION HEADERS - light and dark, swapped by <picture>
# ════════════════════════════════════════════════════════════════════════════
SECTIONS = [("01", "ABOUT", "who I am"), ("02", "WORK", "shipping in production"),
            ("03", "STACK", "what I build with"), ("04", "PROJECTS", "selected work"),
            ("05", "ACTIVITY", "live from the GitHub API")]


def build_headers():
    # The space before a section is IN the image: GitHub's paragraph margins
    # are uniform, so this is what makes a section break read as one.
    W, h, base = PAGE, 92, 70
    for num, title, note in SECTIONS:
        for theme, ink in (("dark", TEXT), ("light", "#0F172A")):
            tx = PAD + 82
            lx = tx + text_width(title, 21, bold=True) + len(title) * 5 + 28
            note_w = text_width(note, T_SMALL, mono=True)
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" '
                f'aria-label="{num} {title}"><defs>'
                f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{INDIGO}"/>'
                f'<stop offset="1" stop-color="{CYAN}"/></linearGradient>'
                f'<linearGradient id="fade" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{INDIGO}" '
                f'stop-opacity="0.7"/><stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></linearGradient></defs>'
                f'<text x="{PAD + 2}" y="{base}" font-family="{MONO}" font-size="27" font-weight="800" fill="url(#g)">{num}</text>'
                f'<rect x="{PAD + 56}" y="{base - 25}" width="2.4" height="30" rx="1.2" fill="{DIM}" fill-opacity="0.5"/>'
                f'<text x="{tx}" y="{base - 1}" font-family="{SANS}" font-size="21" font-weight="800" letter-spacing="5" '
                f'fill="{ink}">{title}</text>'
                f'<rect x="{lx:.0f}" y="{base - 9}" width="{W - PAD - lx - note_w - 30:.0f}" height="1.6" fill="url(#fade)"/>'
                f'<text x="{W - PAD - 2}" y="{base - 3}" text-anchor="end" font-family="{MONO}" font-size="{T_SMALL}" '
                f'fill="{DIM}">{esc(note)}</text></svg>')
            write(f"h-{num}-{theme}.svg", svg)


# ════════════════════════════════════════════════════════════════════════════
#  CONTACT BUTTONS
# ════════════════════════════════════════════════════════════════════════════
BUTTONS = [("linkedin", "in", "LinkedIn", "#0A66C2"), ("portfolio", "globe", "Portfolio", INDIGO),
           ("email", "mail", "Email me", ROSE), ("github", "link", "All repos", CYAN)]


def build_buttons():
    cw, h = QUARTER, 76
    for slug, ic, label, color in BUTTONS:
        solid = ic == "in"
        p = [
            f'<rect x="14" y="14" width="48" height="48" rx="13" fill="{color}" fill-opacity="{1 if solid else 0.16}" '
            f'stroke="{color}" stroke-opacity="0.5"/>',
            icon(ic, 38, 38, "#FFFFFF" if solid else color, scale=1.0, sw=2),
            f'<text x="78" y="46" font-family="{SANS}" font-size="21" font-weight="750" fill="{TEXT}">{esc(label)}</text>',
            arrow(cw - 44, 26, DIM, 0.95),
        ]
        write(f"btn-{slug}.svg", card(cw, h, "".join(p), label=label, grid=False, rx=16,
                                      glows=((38, 38, 100, color, 0.45),)))


# ════════════════════════════════════════════════════════════════════════════
#  LIVE CARDS
# ════════════════════════════════════════════════════════════════════════════
QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100) {
      totalCount
      nodes {
        name
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount weekday } }
      }
    }
  }
}"""


def fetch():
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": QUERY, "variables": {"login": LOGIN}}).encode(),
            headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                     "User-Agent": f"{LOGIN}-profile-builder"})
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.load(r)
    else:
        raw = subprocess.run(["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"login={LOGIN}"],
                             check=True, capture_output=True, text=True).stdout
        payload = json.loads(raw)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]["user"]


def longest_streak(days):
    best = run = 0
    for d in sorted(days, key=lambda d: d["date"]):
        run = run + 1 if d["contributionCount"] > 0 else 0
        best = max(best, run)
    return best


def build_activity(u):
    cal = u["contributionsCollection"]["contributionCalendar"]
    weeks = cal["weeks"]
    days = [d for wk in weeks for d in wk["contributionDays"]]
    active = sum(1 for d in days if d["contributionCount"] > 0)
    longest = longest_streak(days)
    by_month = {}
    for d in days:
        by_month[d["date"][:7]] = by_month.get(d["date"][:7], 0) + d["contributionCount"]
    best_key = max(by_month, key=by_month.get) if by_month else None
    best_n = by_month.get(best_key, 0)
    best_name = dt.date.fromisoformat(best_key + "-01").strftime("%b") if best_key else "-"
    nonzero = sorted(d["contributionCount"] for d in days if d["contributionCount"] > 0)

    def level(n):
        if n == 0 or not nonzero:
            return 0
        q = [nonzero[int(len(nonzero) * f)] for f in (0.25, 0.5, 0.75)]
        return 1 if n <= q[0] else 2 if n <= q[1] else 3 if n <= q[2] else 4

    cw, pad = FULL, 40
    p = [card_header("Contribution activity", "last 12 months", cw)]

    def plural(n, w):
        return w if n == 1 else w + "s"

    metrics = [(f"{cal['totalContributions']:,}", "", "CONTRIBUTIONS", INDIGO),
               (f"{active}", plural(active, "day"), "ACTIVE", VIOLET),
               (f"{best_n}", f"in {best_name}", "BEST MONTH", CYAN),
               (f"{longest}", plural(longest, "day"), "LONGEST STREAK", PINK)]
    tw_ = (cw - 2 * pad - 3 * 20) / 4
    for i, (val, unit, lab, col) in enumerate(metrics):
        x = pad + i * (tw_ + 20)
        p += [
            f'<rect x="{x:.1f}" y="102" width="{tw_:.1f}" height="108" rx="15" fill="#FFFFFF" fill-opacity="0.03" '
            f'stroke="#FFFFFF" stroke-opacity="0.07"/>',
            f'<rect x="{x:.1f}" y="102" width="{tw_:.1f}" height="3" rx="1.5" fill="{col}"/>',
            f'<text x="{x + 24:.1f}" y="164" font-family="{SANS}" font-size="46" font-weight="800" fill="{TEXT}">{esc(val)}</text>',
        ]
        if unit:
            vw = text_width(val, 46, bold=True)
            p.append(f'<text x="{x + 36 + vw:.1f}" y="164" font-family="{SANS}" font-size="18" fill="{DIM}">{esc(unit)}</text>')
        p.append(f'<text x="{x + 24:.1f}" y="192" font-family="{MONO}" font-size="{T_LABEL}" letter-spacing="1.8" '
                 f'fill="{col}">{lab}</text>')

    n = len(weeks)
    gx, gy = pad + 52, 268
    step = (cw - pad - gx) / n
    cell = step - 4.4
    last_m = None
    for wi, wk in enumerate(weeks):
        first = wk["contributionDays"][0]["date"]
        if first[5:7] != last_m:
            last_m = first[5:7]
            if wi < n - 2:
                p.append(f'<text x="{gx + wi * step:.1f}" y="{gy - 14}" font-family="{MONO}" font-size="{T_SMALL}" '
                         f'fill="{DIM}">{dt.date.fromisoformat(first).strftime("%b")}</text>')
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        p.append(f'<text x="{gx - 14}" y="{gy + row * step + cell * 0.78:.1f}" text-anchor="end" '
                 f'font-family="{MONO}" font-size="{T_SMALL}" fill="{DIM}">{lab}</text>')
    for wi, wk in enumerate(weeks):
        for d in wk["contributionDays"]:
            p.append(f'<rect x="{gx + wi * step:.1f}" y="{gy + d["weekday"] * step:.1f}" width="{cell:.1f}" '
                     f'height="{cell:.1f}" rx="3.6" fill="{HEAT[level(d["contributionCount"])]}"/>')
    ly = gy + 7 * step + 26
    p.append(f'<text x="{gx}" y="{ly + 14}" font-family="{MONO}" font-size="{T_SMALL}" fill="{DIM}">'
             f'{cal["totalContributions"]:,} contributions · on GitHub since {u["createdAt"][:4]}</text>')
    lx = cw - pad - 5 * 22 - 52
    p.append(f'<text x="{lx - 12}" y="{ly + 14}" text-anchor="end" font-family="{MONO}" font-size="{T_SMALL}" '
             f'fill="{DIM}">Less</text>')
    for i, c in enumerate(HEAT):
        p.append(f'<rect x="{lx + i * 22}" y="{ly}" width="18" height="18" rx="4" fill="{c}"/>')
    p.append(f'<text x="{lx + 5 * 22 + 6}" y="{ly + 14}" font-family="{MONO}" font-size="{T_SMALL}" fill="{DIM}">More</text>')
    h = ly + 52
    write("activity.svg", card(cw, h, "".join(p), label=f"{cal['totalContributions']} contributions in the last year",
                               glows=((1150, 60, 340, INDIGO, 0.18), (60, h, 340, CYAN, 0.12))))


def build_languages(u):
    totals, colors = {}, {}
    counted = [r for r in u["repositories"]["nodes"] if r["name"] not in VENDORED]
    for repo in counted:
        for e in repo["languages"]["edges"]:
            name = e["node"]["name"]
            if name == "Jupyter Notebook":
                continue  # notebooks store outputs as bytes and drown everything else
            totals[name] = totals.get(name, 0) + e["size"]
            colors[name] = e["node"]["color"] or DIM
    total = sum(totals.values()) or 1
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    top, rest = ranked[:7], sum(v for _, v in ranked[7:])
    if rest:
        top.append(("Other", rest))
        colors["Other"] = "#475569"
    cw, pad = FULL, 40
    p = [card_header("Languages", f"by bytes, across {len(counted)} repositories I wrote", cw)]
    bw, by = cw - 2 * pad, 104
    segs, x = [], pad
    for name, v in top:
        sw = bw * v / total
        segs.append(f'<rect x="{x:.2f}" y="{by}" width="{sw + 0.6:.2f}" height="16" fill="{colors[name]}"/>')
        x += sw
    p.append(f'<clipPath id="bar"><rect x="{pad}" y="{by}" width="{bw}" height="16" rx="8"/></clipPath>'
             f'<g clip-path="url(#bar)">{"".join(segs)}</g>')
    cols, colw = 4, bw / 4
    for i, (name, v) in enumerate(top):
        x = pad + (i % cols) * colw
        y = 168 + (i // cols) * 40
        p += [f'<circle cx="{x + 8:.1f}" cy="{y - 6}" r="7" fill="{colors[name]}"/>',
              f'<text x="{x + 26:.1f}" y="{y}" font-family="{SANS}" font-size="19" font-weight="650" fill="{TEXT}">{esc(name)}</text>',
              f'<text x="{x + colw - 28:.1f}" y="{y}" text-anchor="end" font-family="{MONO}" font-size="{T_SMALL + 1}" '
              f'fill="{DIM}">{v * 100 / total:.1f}%</text>']
    h = 168 + ((len(top) - 1) // cols) * 40 + 40
    write("languages.svg", card(cw, h, "".join(p), label="Languages by bytes",
                                glows=((1100, h, 320, VIOLET, 0.14),)))


def build_footer():
    cw, h = FULL, 84
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    p = (f'<circle cx="40" cy="42" r="5.5" fill="{GREEN}"/>'
         f'<text x="58" y="47.5" font-family="{MONO}" font-size="{T_SMALL}" fill="{MUTED}">Every card here is drawn by '
         f'<tspan fill="{TEXT}">scripts/build.py</tspan> and refreshed nightly.</text>'
         f'<text x="{cw - 36}" y="47.5" text-anchor="end" font-family="{MONO}" font-size="{T_SMALL}" '
         f'fill="{DIM}">built {esc(stamp)}</text>')
    write("footer.svg", card(cw, h, p, label="Footer", grid=False, rx=16, glows=((40, 42, 130, GREEN, 0.2),)))


def main():
    print("static cards")
    build_hero()
    build_about()
    build_work_nxaride()
    build_work_dendo()
    build_store_buttons()
    build_stack()
    for pr in PROJECTS:
        build_project(pr)
    build_headers()
    build_buttons()

    print("live cards")
    try:
        u = fetch()
    except Exception as e:  # noqa: BLE001 - any failure here means "keep yesterday's"
        print(f"  ! GitHub API unavailable ({e}); keeping the existing live cards", file=sys.stderr)
        return
    build_activity(u)
    build_languages(u)
    build_footer()


if __name__ == "__main__":
    main()
