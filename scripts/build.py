#!/usr/bin/env python3
"""
Builds every image in assets/ for the github.com/TheParthi profile README.

Nothing on the profile comes from a third-party card service. Those render in
their own styles, rate-limit, and go blank when the shared deployment is busy;
these are drawn here, in one palette, and committed to the repo.

  static   about, stack, projects, section headers, buttons
           - pure layout, rebuilt every run so a design change is one edit here
  live     activity, languages, footer
           - read from the GitHub GraphQL API

Run nightly by .github/workflows/profile.yml. Locally:  python3 scripts/build.py

Auth: GITHUB_TOKEN when set (the Action); otherwise the `gh` CLI, so a local run
never has to handle a token at all. If the API is unreachable the live cards
are LEFT AS THEY ARE rather than overwritten with zeros.

Standard library only - the Action needs no pip install.
"""
from __future__ import annotations

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

# Repos that are someone else's code committed in bulk. Left in, they were 92%
# of every byte counted, so the language bar described Hugging Face and BNB
# Chain rather than the person whose profile it is.
#   transformers    - Hugging Face's library ("Copyright 2020 The HuggingFace
#                     Team"), committed by `root`; 62 MB, 59.8% of all bytes
#   chain_tool_kit  - a BNB Chain toolkit, 22 MB in 3 commits; 31.9%
VENDORED = {"transformers", "chain_tool_kit"}
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

# ── Design tokens ────────────────────────────────────────────────────────────
BG0, BG1 = "#0B1120", "#111A33"
TEXT, MUTED, DIM, FAINT = "#E2E8F0", "#94A3B8", "#64748B", "#1E293B"
INDIGO, VIOLET, CYAN = "#6366F1", "#A855F7", "#06B6D4"
PINK, GREEN, AMBER, ROSE, BLUE, ORANGE = (
    "#F472B6", "#34D399", "#FBBF24", "#FB7185", "#60A5FA", "#FB923C")

SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"

# Heatmap ramp: empty, then four levels from deep indigo to cyan.
HEAT = ["#172036", "#312E81", "#4F46E5", "#818CF8", "#67E8F9"]


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def text_width(s: str, size: float, mono=False, bold=False) -> float:
    """SVG cannot measure text, so estimate. Generous on purpose: an
    overestimate leaves a little air, an underestimate clips a word."""
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


# ── Shared frame ─────────────────────────────────────────────────────────────
def card(w, h, inner, *, label, accent=None, glows=(), grid=True, extra_defs="", rx=18):
    """A dark rounded panel. Every card on the profile goes through this, which
    is what makes them read as one system rather than a collage."""
    defs = [
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{BG0}"/><stop offset="0.6" stop-color="{BG1}"/>'
        f'<stop offset="1" stop-color="{BG0}"/></linearGradient>',
        '<pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">'
        '<path d="M32 0H0V32" fill="none" stroke="#FFFFFF" stroke-opacity="0.04"/></pattern>',
        '<filter id="blur" x="-100%" y="-100%" width="300%" height="300%">'
        '<feGaussianBlur stdDeviation="48"/></filter>',
        f'<clipPath id="clip"><rect width="{w}" height="{h}" rx="{rx}"/></clipPath>',
        extra_defs,
    ]
    if accent:
        defs.append(
            f'<linearGradient id="acc" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{accent[0]}"/>'
            f'<stop offset="1" stop-color="{accent[1]}"/></linearGradient>')
    body = [f'<rect width="{w}" height="{h}" fill="url(#bg)"/>']
    if grid:
        body.append(f'<rect width="{w}" height="{h}" fill="url(#grid)"/>')
    if glows:
        body.append('<g filter="url(#blur)">' + "".join(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{c}" fill-opacity="{o}"/>'
            for cx, cy, r, c, o in glows) + "</g>")
    if accent:
        body.append(f'<rect width="{w}" height="3" fill="url(#acc)"/>')
    body.append(inner)
    body.append(
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="{rx - 0.5}" '
        f'fill="none" stroke="#FFFFFF" stroke-opacity="0.09"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{esc(label)}">'
        f'<defs>{"".join(defs)}</defs>'
        f'<g clip-path="url(#clip)">{"".join(body)}</g></svg>')


def chip(x, y, label, color, size=13.5, h=30):
    w = text_width(label, size, mono=True) + 36
    svg = (
        f'<g transform="translate({x:.1f},{y:.1f})">'
        f'<rect width="{w:.1f}" height="{h}" rx="{h / 2}" fill="{color}" fill-opacity="0.11" '
        f'stroke="{color}" stroke-opacity="0.40"/>'
        f'<circle cx="15" cy="{h / 2}" r="3.8" fill="{color}"/>'
        f'<text x="26" y="{h / 2 + size * 0.36:.1f}" font-family="{MONO}" '
        f'font-size="{size}" fill="{TEXT}">{esc(label)}</text></g>')
    return svg, w


def write(name: str, svg: str):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(svg, encoding="utf-8")
    print(f"  wrote assets/{name}")


# ── Icons (24-unit grid, stroked) ────────────────────────────────────────────
ICONS = {
    "shield": '<path d="M12 3l7 3v6c0 4.6-3 8-7 9-4-1-7-4.4-7-9V6l7-3z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
    "spark": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M18.5 15.5l.8 1.9 1.9.8-1.9.8-.8 1.9-.8-1.9-1.9-.8 1.9-.8z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 7.5l8.5 6 8.5-6"/>',
    "link": '<path d="M10 14a4.2 4.2 0 0 0 6 0l3-3a4.2 4.2 0 0 0-6-6l-1.2 1.2"/><path d="M14 10a4.2 4.2 0 0 0-6 0l-3 3a4.2 4.2 0 0 0 6 6l1.2-1.2"/>',
    "cloud": '<path d="M7 18.5h10.2a4.2 4.2 0 0 0 .6-8.4A6.2 6.2 0 0 0 6 9.6a4.5 4.5 0 0 0 1 8.9z"/><path d="M12 15.5v-5M9.8 12.6L12 10.4l2.2 2.2"/>',
    "trend": '<path d="M3 17.5l6-6 4 4 8-8"/><path d="M14.5 7.5H21v6.5"/>',
    "brief": '<rect x="3" y="7.5" width="18" height="12.5" rx="2.5"/><path d="M8.5 7.5V5.8A1.8 1.8 0 0 1 10.3 4h3.4a1.8 1.8 0 0 1 1.8 1.8v1.7"/><path d="M3 13h18"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.6 2.7 3.9 5.7 3.9 9s-1.3 6.3-3.9 9c-2.6-2.7-3.9-5.7-3.9-9s1.3-6.3 3.9-9z"/>',
    "in": None,  # drawn as text
}


def icon(name, cx, cy, color, scale=1.15, sw=1.8):
    if name == "in":
        return (f'<text x="{cx}" y="{cy + 7}" text-anchor="middle" font-family="{SANS}" '
                f'font-size="21" font-weight="800" fill="{color}">in</text>')
    return (f'<g transform="translate({cx - 12 * scale:.1f},{cy - 12 * scale:.1f}) scale({scale})" '
            f'fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="round" '
            f'stroke-linejoin="round">{ICONS[name]}</g>')


# ════════════════════════════════════════════════════════════════════════════
#  STATIC CARDS
# ════════════════════════════════════════════════════════════════════════════

# ── About: an editor window ──────────────────────────────────────────────────
CODE = [
    '/** Ships AI-powered, security-first products \u2014 end to end. */',
    '@Profile(focus = { "Mobility", "AI", "Security" })',
    'public final class Parthiban extends FullStackEngineer {',
    '',
    '    String   building   = "NXA Ride @ Dendo";',
    '    String[] languages  = { "Java", "TypeScript", "Python", "SQL" };',
    '    String[] backend    = { "Spring Boot", "NestJS", "Prisma", "Socket.IO" };',
    '    String[] mobile     = { "React Native", "React", "Next.js" };',
    '    String[] ai         = { "GenAI", "LangChain", "LangGraph", "RAG" };',
    '    String[] cloud      = { "AWS", "Azure", "Docker", "GitHub Actions" };',
    '',
    '    boolean  openToWork = true;   // SDE · Full-Stack roles',
    '}',
]

SYNTAX = re.compile(
    r'(?P<cm>/\*\*.*?\*/|//.*$)'
    r'|(?P<st>"[^"]*")'
    r'|(?P<an>@\w+)'
    r'|(?P<bo>\b(?:true|false|null)\b|\b\d+\b)'
    r'|(?P<kw>\b(?:public|private|final|static|class|extends|implements|boolean|return|new)\b)'
    r'|(?P<ty>\b[A-Z]\w*(?:\[\])?)'
    r'|(?P<fd>\b[a-z]\w*(?=\s*=))'
    r'|(?P<pu>[^\w\s"@/]+|/)'
    r'|(?P<sp>\s+)'
    r'|(?P<tx>\w+)')
TOKEN_COLOR = {"cm": "#64748B", "st": "#86EFAC", "an": "#FBBF24", "bo": "#F472B6",
               "kw": "#C084FC", "ty": "#67E8F9", "fd": "#93C5FD", "pu": "#94A3B8",
               "sp": None, "tx": TEXT}


def highlight(line: str) -> str:
    out = []
    for m in SYNTAX.finditer(line):
        kind = m.lastgroup
        tok = esc(m.group())
        color = TOKEN_COLOR[kind]
        if color is None:
            out.append(tok)
        elif kind == "cm":
            out.append(f'<tspan fill="{color}" font-style="italic">{tok}</tspan>')
        else:
            out.append(f'<tspan fill="{color}">{tok}</tspan>')
    return "".join(out)


def build_about():
    w, top, lh, fs = 1200, 48, 31, 17
    first = top + 44
    status_h = 34
    h = first + (len(CODE) - 1) * lh + 30 + status_h
    gutter_x, code_x = 58, 86
    parts = []

    # Title bar
    parts.append(f'<rect width="{w}" height="{top}" fill="#FFFFFF" fill-opacity="0.025"/>')
    parts.append(f'<rect y="{top - 1}" width="{w}" height="1" fill="#FFFFFF" fill-opacity="0.07"/>')
    for i, c in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        parts.append(f'<circle cx="{26 + i * 20}" cy="{top / 2}" r="6.2" fill="{c}"/>')
    # Active tab
    parts.append(f'<rect x="96" y="9" width="200" height="{top - 9}" rx="8" fill="{BG0}"/>')
    parts.append(f'<rect x="96" y="{top - 2}" width="200" height="2" fill="url(#tabline)"/>')
    parts.append(f'<circle cx="116" cy="{top / 2 + 4}" r="4.5" fill="{ORANGE}"/>')
    parts.append(f'<text x="128" y="{top / 2 + 9}" font-family="{MONO}" font-size="13.5" fill="{TEXT}">Parthiban.java</text>')
    parts.append(f'<text x="312" y="{top / 2 + 9}" font-family="{MONO}" font-size="13.5" fill="{DIM}">Projects.java</text>')
    parts.append(f'<text x="{w - 24}" y="{top / 2 + 5}" text-anchor="end" font-family="{MONO}" '
                 f'font-size="12.5" fill="{DIM}" xml:space="preserve">UTF-8   ·   LF   ·   Java 21</text>')

    # Current-line highlight on the openToWork line
    hl = len(CODE) - 2
    parts.append(f'<rect x="0" y="{first + hl * lh - lh * 0.72:.1f}" width="{w}" height="{lh}" '
                 f'fill="{INDIGO}" fill-opacity="0.09"/>')
    parts.append(f'<rect x="0" y="{first + hl * lh - lh * 0.72:.1f}" width="3" height="{lh}" fill="{INDIGO}"/>')

    # Code
    for i, line in enumerate(CODE):
        y = first + i * lh
        num_color = TEXT if i == hl else "#3B4A63"
        parts.append(f'<text x="{gutter_x}" y="{y}" text-anchor="end" font-family="{MONO}" '
                     f'font-size="{fs - 2}" fill="{num_color}">{i + 1}</text>')
        if line:
            parts.append(f'<text x="{code_x}" y="{y}" font-family="{MONO}" font-size="{fs}" '
                         f'xml:space="preserve" fill="{TEXT}">{highlight(line)}</text>')

    # Blinking caret after the final brace
    cy = first + (len(CODE) - 1) * lh
    parts.append(f'<rect x="{code_x + 14}" y="{cy - 17}" width="2.4" height="22" fill="#67E8F9">'
                 f'<animate attributeName="opacity" values="1;1;0;0" keyTimes="0;0.5;0.5;1" '
                 f'dur="1.1s" repeatCount="indefinite"/></rect>')

    # Minimap
    mx = w - 128
    parts.append(f'<rect x="{mx - 16}" y="{top}" width="1" height="{h - top - status_h}" fill="#FFFFFF" fill-opacity="0.05"/>')
    for i, line in enumerate(CODE):
        if not line.strip():
            continue
        indent = (len(line) - len(line.lstrip())) * 1.1
        bw = min(len(line.strip()) * 1.25, 96 - indent)
        color = "#64748B" if line.strip().startswith("/") else (INDIGO if i == hl else "#475569")
        parts.append(f'<rect x="{mx + indent:.1f}" y="{top + 18 + i * 7}" width="{bw:.1f}" height="3.2" '
                     f'rx="1.6" fill="{color}" fill-opacity="0.8"/>')
    parts.append(f'<rect x="{mx - 6}" y="{top + 12}" width="110" height="{len(CODE) * 7 + 12}" rx="4" '
                 f'fill="#FFFFFF" fill-opacity="0.035" stroke="#FFFFFF" stroke-opacity="0.06"/>')

    # Status bar
    sy = h - status_h
    parts.append(f'<rect y="{sy}" width="{w}" height="{status_h}" fill="url(#status)"/>')
    st = sy + status_h / 2 + 4.5
    parts.append(icon("brief", 30, sy + status_h / 2, "#FFFFFF", scale=0.62, sw=2.2).replace(ICONS["brief"], '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><circle cx="6" cy="18" r="2.5"/><path d="M6 8.5v7M18 15.5V12a3 3 0 0 0-3-3H9"/>'))
    parts.append(f'<text x="46" y="{st}" font-family="{MONO}" font-size="12.5" fill="#FFFFFF">main</text>')
    parts.append(f'<text x="104" y="{st}" font-family="{MONO}" font-size="12.5" fill="#FFFFFF" fill-opacity="0.85">✓ 0 problems</text>')
    parts.append(f'<text x="{w - 24}" y="{st}" text-anchor="end" font-family="{MONO}" font-size="12.5" '
                 f'fill="#FFFFFF" fill-opacity="0.9" xml:space="preserve">Ln {len(CODE)}, Col 2    Spaces: 4    Java</text>')

    defs = (f'<linearGradient id="tabline" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{INDIGO}"/>'
            f'<stop offset="1" stop-color="{CYAN}"/></linearGradient>'
            f'<linearGradient id="status" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#4F46E5"/>'
            f'<stop offset="0.55" stop-color="#7C3AED"/><stop offset="1" stop-color="#0891B2"/></linearGradient>')
    write("about.svg", card(w, h, "".join(parts), label="About Parthiban, written as a Java class",
                            glows=((1050, 120, 220, VIOLET, 0.20), (160, h - 60, 200, INDIGO, 0.16)),
                            grid=False, extra_defs=defs, rx=16))


# ── Stack ────────────────────────────────────────────────────────────────────
STACK = [
    ("LANGUAGES", INDIGO, ["Java", "TypeScript", "Python", "SQL"]),
    ("BACKEND", VIOLET, ["Spring Boot", "NestJS", "Prisma", "Socket.IO", "REST · JWT"]),
    ("MOBILE  ·  WEB", CYAN, ["React Native", "React", "Next.js", "Tailwind CSS"]),
    ("AI  ·  LLM", PINK, ["GenAI", "LangChain", "LangGraph", "RAG", "Genkit", "ChromaDB"]),
    ("DATA", GREEN, ["PostgreSQL", "PostGIS", "Redis", "MySQL", "Supabase"]),
    ("CLOUD  ·  DEVOPS", AMBER, ["AWS", "Azure", "Docker", "GitHub Actions", "nginx"]),
]


def build_stack():
    w, pad, cols, gap = 1200, 40, 3, 28
    cell = (w - 2 * pad - (cols - 1) * gap) / cols
    row_h = 40
    blocks, heights = [], []
    for title, color, items in STACK:
        lines, x, y = [], 0, 0
        out = []
        for it in items:
            cw = text_width(it, 13.5, mono=True) + 36
            if x and x + cw > cell:
                x, y = 0, y + row_h
            s, cw = chip(x, y, it, color)
            out.append(s)
            x += cw + 10
        heights.append(y + row_h)
        blocks.append((title, color, "".join(out)))

    parts, top = [], 40
    rows = [heights[i:i + cols] for i in range(0, len(heights), cols)]
    row_tops, yy = [], top
    for r in rows:
        row_tops.append(yy)
        yy += 34 + max(r) + 26
    h = yy + 8

    for i, (title, color, chips) in enumerate(blocks):
        cx = pad + (i % cols) * (cell + gap)
        cy = row_tops[i // cols]
        parts.append(f'<rect x="{cx}" y="{cy + 2}" width="3" height="14" rx="1.5" fill="{color}"/>')
        parts.append(f'<text x="{cx + 12}" y="{cy + 14}" font-family="{MONO}" font-size="12.5" '
                     f'letter-spacing="2.2" font-weight="700" fill="{color}">{esc(title)}</text>')
        parts.append(f'<g transform="translate({cx:.1f},{cy + 30})">{chips}</g>')
    write("stack.svg", card(w, h, "".join(parts), label="Tech stack",
                            glows=((1100, 0, 200, CYAN, 0.14), (80, h, 200, VIOLET, 0.12))))


# ── Projects ─────────────────────────────────────────────────────────────────
PROJECTS = [
    dict(slug="malware", repo="Malware-Detection-GenAI", title="Malware Detection · GenAI",
         kicker="GENAI  ·  CYBERSECURITY", icon="shield", color=ROSE, lang=("Python", "#3572A5"),
         desc="Static analysis of uploaded files, Malware/Benign classification "
              "with GenAI decision logic, and automated incident response.",
         chips=["Python", "GenAI", "Static analysis"]),
    dict(slug="email-rag", repo="Email_rag", title="AI Support Email Agents",
         kicker="AGENTS  ·  RAG", icon="mail", color=VIOLET, lang=("Python", "#3572A5"),
         desc="Customer-support email automation on LangGraph agents: categorises "
              "mail, synthesises queries and answers with RAG over the Gmail API.",
         chips=["LangGraph", "ChromaDB", "Gemini"]),
    dict(slug="loanify", repo="Loanify", title="Loanify",
         kicker="AI  ·  FINTECH", icon="trend", color=AMBER, lang=("TypeScript", "#3178C6"),
         desc="AI-driven NBFC loan automation with separate customer and admin "
              "interfaces and instant eligibility checks.",
         chips=["Next.js", "Genkit", "Firebase"]),
    dict(slug="security-monitor", repo="Security_monitor", title="Blockchain Security Monitor",
         kicker="WEB3  ·  LLM RISK", icon="link", color=CYAN, lang=("Python", "#3572A5"),
         desc="Monitors ERC-20 allowances, flags high-risk approvals and uses LLMs "
              "(DeepSeek, Gemini, OpenAI) to explain the risk.",
         chips=["Python", "ERC-20", "LLMs"]),
    dict(slug="trustlance", repo="Freelancer_Marketplace_TrustLance", title="TrustLance",
         kicker="MARKETPLACE  ·  FULL STACK", icon="brief", color=GREEN, lang=("TypeScript", "#3178C6"),
         desc="Freelance marketplace with role-based access and authenticated "
              "APIs, built on Next.js with Supabase and Firebase.",
         chips=["Next.js", "Supabase", "Tailwind"]),
    dict(slug="cloud-backup", repo="secure-cloud-storage-backup-system", title="Secure Cloud Backup",
         kicker="CLOUD  ·  DATA PROTECTION", icon="cloud", color=ORANGE, lang=("AWS", ORANGE),
         desc="A data-protection-first storage and backup system on AWS, built "
              "around S3 and EC2 for availability and recovery.",
         chips=["AWS", "S3", "EC2"]),
]


def build_project(p):
    w, h, pad = 600, 318, 32
    c = p["color"]
    parts = []
    # Icon tile
    parts.append(f'<rect x="{pad}" y="{pad}" width="56" height="56" rx="15" fill="{c}" fill-opacity="0.13" '
                 f'stroke="{c}" stroke-opacity="0.45"/>')
    parts.append(icon(p["icon"], pad + 28, pad + 28, c))
    # Title + kicker
    parts.append(f'<text x="{pad + 76}" y="{pad + 24}" font-family="{MONO}" font-size="12" letter-spacing="2" '
                 f'font-weight="700" fill="{c}">{esc(p["kicker"])}</text>')
    title_size = 26 if text_width(p["title"], 26, bold=True) < w - pad * 2 - 76 else 22
    parts.append(f'<text x="{pad + 76}" y="{pad + 52}" font-family="{SANS}" font-size="{title_size}" '
                 f'font-weight="750" fill="{TEXT}">{esc(p["title"])}</text>')
    # Arrow, top right
    parts.append(f'<g transform="translate({w - pad - 22},{pad + 4})" fill="none" stroke="{MUTED}" '
                 f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                 f'<path d="M5 17L17 5"/><path d="M8 5h9v9"/></g>')
    # Description
    for i, line in enumerate(wrap(p["desc"], 56)[:3]):
        parts.append(f'<text x="{pad}" y="{pad + 104 + i * 27}" font-family="{SANS}" font-size="17" '
                     f'fill="{MUTED}">{esc(line)}</text>')
    # Chips
    x = pad
    for it in p["chips"]:
        s, cw = chip(x, 212, it, c, size=13)
        parts.append(s)
        x += cw + 10
    # Footer
    parts.append(f'<rect x="{pad}" y="{h - 52}" width="{w - 2 * pad}" height="1" fill="#FFFFFF" fill-opacity="0.07"/>')
    repo = f"{LOGIN}/{p['repo']}"
    if len(repo) > 50:
        repo = repo[:49] + "…"
    parts.append(f'<text x="{pad}" y="{h - 22}" font-family="{MONO}" font-size="13" fill="{DIM}">{esc(repo)}</text>')
    lname, lcolor = p["lang"]
    lw = text_width(lname, 13, mono=True)
    parts.append(f'<circle cx="{w - pad - lw - 14}" cy="{h - 26.5}" r="5" fill="{lcolor}"/>')
    parts.append(f'<text x="{w - pad}" y="{h - 22}" text-anchor="end" font-family="{MONO}" font-size="13" '
                 f'fill="{MUTED}">{esc(lname)}</text>')
    write(f"project-{p['slug']}.svg",
          card(w, h, "".join(parts), label=f"{p['title']} — {p['desc']}",
               accent=(c, INDIGO), glows=((w - 40, 30, 150, c, 0.22),)))


# ── Section headers (light + dark, swapped by <picture>) ─────────────────────
SECTIONS = [("01", "ABOUT", "who I am"), ("02", "WORK", "shipping in production"),
            ("03", "STACK", "what I build with"), ("04", "PROJECTS", "selected work"),
            ("05", "ACTIVITY", "live from the GitHub API")]


def build_headers():
    w, h = 1200, 64
    for num, title, note in SECTIONS:
        for theme, ink, muted in (("dark", TEXT, DIM), ("light", "#0F172A", "#64748B")):
            tx = 74
            tw_ = text_width(title, 17, bold=True) + len(title) * 4.2
            line_x = tx + tw_ + 26
            note_w = text_width(note, 13, mono=True)
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
                f'role="img" aria-label="{num} {title}">'
                f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0">'
                f'<stop offset="0" stop-color="{INDIGO}"/><stop offset="1" stop-color="{CYAN}"/></linearGradient>'
                f'<linearGradient id="fade" x1="0" y1="0" x2="1" y2="0">'
                f'<stop offset="0" stop-color="{INDIGO}" stop-opacity="0.65"/>'
                f'<stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></linearGradient></defs>'
                f'<text x="2" y="41" font-family="{MONO}" font-size="22" font-weight="800" fill="url(#g)">{num}</text>'
                f'<rect x="50" y="22" width="2" height="24" rx="1" fill="{muted}" fill-opacity="0.5"/>'
                f'<text x="{tx}" y="40" font-family="{SANS}" font-size="17" font-weight="800" '
                f'letter-spacing="4.2" fill="{ink}">{title}</text>'
                f'<rect x="{line_x:.0f}" y="33" width="{w - line_x - note_w - 30:.0f}" height="1.5" fill="url(#fade)"/>'
                f'<text x="{w - 2}" y="39" text-anchor="end" font-family="{MONO}" font-size="13" '
                f'fill="{muted}">{esc(note)}</text></svg>')
            write(f"h-{num}-{theme}.svg", svg)


# ── Contact buttons ──────────────────────────────────────────────────────────
BUTTONS = [("linkedin", "in", "LinkedIn", "#0A66C2"), ("portfolio", "globe", "Portfolio", INDIGO),
           ("email", "mail", "Email me", ROSE), ("github", "link", "All repos", CYAN)]


def build_buttons():
    w, h = 270, 62
    for slug, ic, label, color in BUTTONS:
        inner = (
            f'<rect x="10" y="10" width="42" height="42" rx="12" fill="{color}" fill-opacity="0.16" '
            f'stroke="{color}" stroke-opacity="0.5"/>'
            f'{icon(ic, 31, 31, color if ic != "in" else "#FFFFFF", scale=0.92, sw=2)}'
            f'<text x="68" y="38" font-family="{SANS}" font-size="18" font-weight="700" fill="{TEXT}">{esc(label)}</text>'
            f'<g transform="translate({w - 40},19)" fill="none" stroke="{DIM}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round"><path d="M5 17L17 5"/><path d="M8 5h9v9"/></g>')
        if ic == "in":
            inner = inner.replace(f'fill="{color}" fill-opacity="0.16"', f'fill="{color}" fill-opacity="1"')
        write(f"btn-{slug}.svg", card(w, h, inner, label=label, grid=False, rx=16,
                                      glows=((40, 31, 60, color, 0.35),)))


# ── Work: NXA Ride and Dendo ─────────────────────────────────────────────────
#
# Logos are embedded as data URIs: an SVG shown through <img> is sandboxed and
# will not fetch anything external, so a linked image would render as nothing.
SRC = OUT / "src"
DENDO_BLUE = "#132E7C"
PLAY = "https://play.google.com/store/apps/details?id="

ICONS.update({
    "phone": '<rect x="7" y="2.5" width="10" height="19" rx="2.6"/><path d="M10.5 18.3h3"/>',
    "pin": '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.4"/>',
    "grid": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="4.5" rx="1.6"/>'
            '<rect x="13.5" y="11" width="7" height="9.5" rx="1.6"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>',
    "server": '<rect x="3.5" y="4" width="17" height="6.5" rx="2"/><rect x="3.5" y="13.5" width="17" height="6.5" rx="2"/>'
              '<path d="M7.5 7.25h.01M7.5 16.75h.01"/>',
})


def data_uri(name: str) -> str:
    import base64
    return "data:image/png;base64," + base64.b64encode((SRC / name).read_bytes()).decode()


def live_pill(x, y, label="LIVE ON GOOGLE PLAY"):
    w = text_width(label, 11.5, mono=True) + 40
    return (f'<g transform="translate({x:.1f},{y})">'
            f'<rect width="{w:.1f}" height="28" rx="14" fill="{GREEN}" fill-opacity="0.12" '
            f'stroke="{GREEN}" stroke-opacity="0.45"/>'
            f'<circle cx="16" cy="14" r="4.3" fill="{GREEN}"><animate attributeName="opacity" '
            f'values="1;0.25;1" dur="2.2s" repeatCount="indefinite"/></circle>'
            f'<text x="28" y="18.2" font-family="{MONO}" font-size="11.5" letter-spacing="1" '
            f'fill="#6EE7B7">{label}</text></g>'), w


# The route the vehicle drives, in map-panel coordinates. Pickup and drop are
# Madiwala and Silk Board - the pair NXA Ride is tested against every day.
ROUTE = "M70 318 L70 212 Q70 196 86 196 L304 196 Q320 196 320 180 L320 102 Q320 86 336 86 L396 86"


def map_vignette(mx, my, mw, mh):
    roads = []
    for y in (86, 196, 286):
        roads.append(f'<path d="M-10 {y}H{mw + 10}" stroke="#1A2540" stroke-width="8"/>')
    for x in (70, 190, 320, 400):
        roads.append(f'<path d="M{x} -10V{mh + 10}" stroke="#1A2540" stroke-width="8"/>')
    roads.append(f'<path d="M-30 350L{mw + 30} 40" stroke="#25335A" stroke-width="14"/>')
    roads.append(f'<path d="M-30 350L{mw + 30} 40" stroke="#33446F" stroke-width="1.2" stroke-dasharray="10 12"/>')
    for y in (86, 196, 286):
        roads.append(f'<path d="M-10 {y}H{mw + 10}" stroke="#222E4C" stroke-width="0.8"/>')

    g = [f'<g transform="translate({mx},{my})" clip-path="url(#mapclip)">',
         f'<rect width="{mw}" height="{mh}" fill="#0C1426"/>',
         f'<path d="M0 300 C40 262 128 268 160 306 C184 336 150 {mh} 84 {mh} L0 {mh}Z" fill="#0B2438"/>',
         f'<rect x="210" y="216" width="92" height="52" rx="10" fill="#0E2620"/>',
         f'<rect x="92" y="104" width="78" height="72" rx="10" fill="#0E2620"/>',
         f'<g fill="none" stroke-linecap="round">{"".join(roads)}</g>',
         # The route: soft glow, solid gradient line, then a dashed sheen running along it.
         f'<path d="{ROUTE}" fill="none" stroke="{INDIGO}" stroke-opacity="0.35" stroke-width="16" '
         f'stroke-linecap="round" stroke-linejoin="round" filter="url(#rblur)"/>',
         f'<path d="{ROUTE}" fill="none" stroke="url(#routeG)" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/>',
         f'<path d="{ROUTE}" fill="none" stroke="#FFFFFF" stroke-opacity="0.75" stroke-width="2" '
         f'stroke-linecap="round" stroke-dasharray="1 13"><animate attributeName="stroke-dashoffset" '
         f'from="0" to="-28" dur="0.9s" repeatCount="indefinite"/></path>',
         # Pickup
         f'<circle cx="70" cy="318" r="18" fill="{GREEN}" fill-opacity="0.18"/>',
         f'<circle cx="70" cy="318" r="9" fill="#22C55E" stroke="#FFFFFF" stroke-width="3"/>',
         f'<g transform="translate(92,304)"><rect width="148" height="28" rx="8" fill="#0B1120" fill-opacity="0.92" '
         f'stroke="#FFFFFF" stroke-opacity="0.12"/><text x="12" y="18.5" font-family="{MONO}" font-size="12" '
         f'fill="{TEXT}"><tspan fill="#4ADE80">●</tspan> Pickup · Madiwala</text></g>',
         # Drop
         f'<g transform="translate(396,86)"><path d="M0 0c-7-8-12-13-12-19.5a12 12 0 0 1 24 0C12-13 7-8 0 0z" '
         f'fill="#F43F5E" stroke="#FFFFFF" stroke-width="2.2"/><circle cy="-19.5" r="4.4" fill="#FFFFFF"/></g>',
         f'<g transform="translate(214,42)"><rect width="160" height="28" rx="8" fill="#0B1120" fill-opacity="0.92" '
         f'stroke="#FFFFFF" stroke-opacity="0.12"/><text x="12" y="18.5" font-family="{MONO}" font-size="12" '
         f'fill="{TEXT}"><tspan fill="#FB7185">●</tspan> Drop · Silk Board</text></g>',
         # Vehicle: hidden unless the renderer animates, so a static render never
         # shows it parked in the corner at 0,0.
         f'<g opacity="0"><set attributeName="opacity" to="1" begin="0s"/>'
         f'<animateMotion path="{ROUTE}" rotate="auto" dur="9s" repeatCount="indefinite" '
         f'keyPoints="0;1;1" keyTimes="0;0.86;1" calcMode="linear"/>'
         f'<circle r="20" fill="#67E8F9" fill-opacity="0.16"><animate attributeName="r" values="14;24;14" '
         f'dur="1.6s" repeatCount="indefinite"/></circle>'
         f'<path d="M11 0L-9 8.5L-4.5 0L-9 -8.5Z" fill="#67E8F9" stroke="#FFFFFF" stroke-width="2" '
         f'stroke-linejoin="round"/></g>',
         # ETA chip
         f'<g transform="translate(18,18)"><rect width="172" height="62" rx="14" fill="#0B1120" fill-opacity="0.94" '
         f'stroke="#FFFFFF" stroke-opacity="0.12"/>'
         f'<text x="16" y="25" font-family="{MONO}" font-size="10.5" letter-spacing="1.6" fill="{DIM}">ARRIVING IN</text>'
         f'<text x="16" y="49" font-family="{SANS}" font-size="20" font-weight="800" fill="{TEXT}">6 min'
         f'<tspan font-size="14" font-weight="500" fill="{MUTED}">  ·  2.4 km</tspan></text></g>',
         # Recenter button
         f'<g transform="translate({mw - 32},{mh - 32})"><circle r="20" fill="#0B1120" stroke="#FFFFFF" '
         f'stroke-opacity="0.15"/><circle r="6" fill="none" stroke="{TEXT}" stroke-width="2"/>'
         f'<path d="M0 -12v4M0 8v4M-12 0h4M8 0h4" stroke="{TEXT}" stroke-width="2" stroke-linecap="round"/></g>',
         f'<rect width="{mw}" height="{mh}" fill="url(#vig)"/>',
         '</g>',
         f'<rect x="{mx + 0.5}" y="{my + 0.5}" width="{mw - 1}" height="{mh - 1}" rx="18" fill="none" '
         f'stroke="#FFFFFF" stroke-opacity="0.12"/>']
    defs = (f'<clipPath id="mapclip"><rect width="{mw}" height="{mh}" rx="18"/></clipPath>'
            f'<linearGradient id="routeG" gradientUnits="userSpaceOnUse" x1="70" y1="318" x2="396" y2="86">'
            f'<stop offset="0" stop-color="#22C55E"/><stop offset="0.45" stop-color="{INDIGO}"/>'
            f'<stop offset="1" stop-color="{CYAN}"/></linearGradient>'
            f'<filter id="rblur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="5"/></filter>'
            f'<radialGradient id="vig" cx="50%" cy="45%" r="75%"><stop offset="0.55" stop-color="#0B1120" stop-opacity="0"/>'
            f'<stop offset="1" stop-color="#0B1120" stop-opacity="0.7"/></radialGradient>')
    return "".join(g), defs


def build_work_nexaride():
    w, h = 1200, 448
    left_w = 650
    parts = []
    # Identity row
    parts.append(f'<clipPath id="logoclip"><rect x="44" y="44" width="72" height="72" rx="18"/></clipPath>')
    parts.append(f'<image href="{data_uri("nxa_logo.png")}" x="44" y="44" width="72" height="72" '
                 f'clip-path="url(#logoclip)" preserveAspectRatio="xMidYMid slice"/>')
    parts.append(f'<rect x="44.5" y="44.5" width="71" height="71" rx="17.5" fill="none" stroke="#FFFFFF" stroke-opacity="0.18"/>')
    parts.append(f'<text x="138" y="66" font-family="{MONO}" font-size="12.5" letter-spacing="2.2" font-weight="700" '
                 f'fill="#60A5FA">PRODUCTION  ·  BUILT AT DENDO</text>')
    parts.append(f'<text x="136" y="108" font-family="{SANS}" font-size="40" font-weight="800" letter-spacing="-0.8" '
                 f'fill="{TEXT}">NXA Ride</text>')
    pill, _ = live_pill(136 + text_width("NXA Ride", 40, bold=True) + 16, 82)
    parts.append(pill)

    # Description
    desc = ("Ride-hailing and parcel delivery across a rider app, driver app, admin console "
            "and backend \u2014 live booking, real-time tracking over WebSockets, parcel "
            "handover codes and UPI payments.")
    for i, line in enumerate(wrap(desc, 66)):
        parts.append(f'<text x="44" y="{166 + i * 27}" font-family="{SANS}" font-size="17" fill="{MUTED}">{esc(line)}</text>')

    # The four surfaces
    surfaces = [("phone", "Rider app", "React Native", INDIGO), ("pin", "Driver app", "React Native", CYAN),
                ("grid", "Admin console", "Next.js", VIOLET), ("server", "Backend API", "NestJS", PINK)]
    tw_ = (left_w - 3 * 12) / 4
    for i, (ic, title, sub, col) in enumerate(surfaces):
        x = 44 + i * (tw_ + 12)
        parts.append(f'<rect x="{x:.1f}" y="262" width="{tw_:.1f}" height="64" rx="14" fill="#FFFFFF" '
                     f'fill-opacity="0.035" stroke="#FFFFFF" stroke-opacity="0.08"/>')
        parts.append(icon(ic, x + 26, 294, col, scale=0.95, sw=1.9))
        parts.append(f'<text x="{x + 48:.1f}" y="290" font-family="{SANS}" font-size="14.5" font-weight="700" fill="{TEXT}">{title}</text>')
        parts.append(f'<text x="{x + 48:.1f}" y="309" font-family="{MONO}" font-size="11.5" fill="{DIM}">{sub}</text>')

    # Stack
    x, y = 44, 350
    for it, col in (("React Native", CYAN), ("NestJS", PINK), ("PostgreSQL + PostGIS", GREEN),
                    ("Redis", ROSE), ("Socket.IO", VIOLET), ("Google Maps", AMBER), ("AWS · Azure", ORANGE)):
        cw = text_width(it, 13, mono=True) + 36
        if x + cw > 44 + left_w:
            x, y = 44, y + 40
        s, cw = chip(x, y, it, col, size=13)
        parts.append(s)
        x += cw + 9

    m, mdefs = map_vignette(726, 34, 440, h - 68)
    parts.append(m)
    write("work-nxaride.svg", card(w, h, "".join(parts), label="NXA Ride - ride-hailing and parcel delivery, built at Dendo",
                                   accent=("#22C55E", CYAN), extra_defs=mdefs,
                                   glows=((900, 40, 260, INDIGO, 0.22), (120, h, 240, CYAN, 0.12))))


def build_work_dendo():
    w, h = 1200, 172
    parts = []
    parts.append(f'<rect x="36" y="37" width="98" height="98" rx="24" fill="{DENDO_BLUE}"/>')
    parts.append(f'<rect x="36.5" y="37.5" width="97" height="97" rx="23.5" fill="none" stroke="#FFFFFF" stroke-opacity="0.2"/>')
    parts.append(f'<image href="{data_uri("dendo_mark.png")}" x="47" y="48" width="76" height="76"/>')
    parts.append(f'<text x="160" y="66" font-family="{MONO}" font-size="12.5" letter-spacing="2.2" font-weight="700" '
                 f'fill="#7C9CF0">THE COMPANY</text>')
    parts.append(f'<text x="158" y="106" font-family="{SANS}" font-size="34" font-weight="800" letter-spacing="-0.6" fill="{TEXT}">Dendo</text>')
    parts.append(f'<text x="160" y="138" font-family="{SANS}" font-size="16.5" fill="{MUTED}">'
                 f'Food, groceries and parcels, delivered daily  ·  NXA Ride is a Dendo product.</text>')
    pill, pw = live_pill(w - 44 - 190 - 40, 72)
    parts.append(pill)
    parts.append(f'<g transform="translate({w - 72},74)" fill="none" stroke="{MUTED}" stroke-width="2" '
                 f'stroke-linecap="round" stroke-linejoin="round"><path d="M5 19L19 5"/><path d="M8 5h11v11"/></g>')
    write("work-dendo.svg", card(w, h, "".join(parts), label="Dendo - food, groceries and parcels, delivered daily",
                                 accent=("#3B5BDB", "#7C9CF0"),
                                 glows=((90, 86, 140, "#3B5BDB", 0.40), (1100, 60, 180, INDIGO, 0.14))))


STORES = [("nxaride", "nxa_logo.png", None, "NXA Ride", "Rider app"),
          ("nxadriver", "nxa_logo.png", None, "NXA Ride Partner", "Driver app"),
          ("dendo", "dendo_mark.png", DENDO_BLUE, "Dendo", "Food & groceries")]


def build_store_buttons():
    w, h = 390, 78
    for slug, logo, tile, name, sub in STORES:
        p = []
        if tile:
            p.append(f'<rect x="13" y="13" width="52" height="52" rx="14" fill="{tile}"/>')
            p.append(f'<image href="{data_uri(logo)}" x="18" y="18" width="42" height="42"/>')
        else:
            p.append(f'<clipPath id="lc"><rect x="13" y="13" width="52" height="52" rx="14"/></clipPath>')
            p.append(f'<image href="{data_uri(logo)}" x="13" y="13" width="52" height="52" clip-path="url(#lc)" '
                     f'preserveAspectRatio="xMidYMid slice"/>')
        p.append(f'<text x="82" y="34" font-family="{MONO}" font-size="10.5" letter-spacing="1.6" fill="{DIM}">'
                 f'GET IT ON GOOGLE PLAY</text>')
        p.append(f'<text x="82" y="58" font-family="{SANS}" font-size="19" font-weight="800" fill="{TEXT}">{esc(name)}'
                 f'<tspan dx="9" font-size="13" font-weight="500" fill="{DIM}">{esc(sub)}</tspan></text>')
        # Play glyph
        p.append(f'<g transform="translate({w - 44},39)"><path d="M-7 -10L10 0L-7 10Z" fill="none" stroke="{MUTED}" '
                 f'stroke-width="2" stroke-linejoin="round"/></g>')
        write(f"store-{slug}.svg", card(w, h, "".join(p), label=f"{name} on Google Play", grid=False, rx=18,
                                        glows=((40, 39, 70, tile or INDIGO, 0.45),)))


# ════════════════════════════════════════════════════════════════════════════
#  LIVE CARDS
# ════════════════════════════════════════════════════════════════════════════
QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100) {
      totalCount
      nodes {
        name
        stargazerCount
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


def streaks(days):
    days = sorted(days, key=lambda d: d["date"])
    longest = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] > 0 else 0
        longest = max(longest, run)
    # The current streak survives a quiet TODAY - the day is not over yet.
    current, i = 0, len(days) - 1
    if i >= 0 and days[i]["contributionCount"] == 0:
        i -= 1
    while i >= 0 and days[i]["contributionCount"] > 0:
        current += 1
        i -= 1
    return current, longest


def build_activity(u):
    cal = u["contributionsCollection"]["contributionCalendar"]
    weeks = cal["weeks"]
    days = [d for wk in weeks for d in wk["contributionDays"]]
    current, longest = streaks(days)
    active = sum(1 for d in days if d["contributionCount"] > 0)
    counts = sorted(d["contributionCount"] for d in days if d["contributionCount"] > 0)

    def level(n):
        if n == 0 or not counts:
            return 0
        q = [counts[int(len(counts) * f)] for f in (0.25, 0.5, 0.75)]
        return 1 if n <= q[0] else 2 if n <= q[1] else 3 if n <= q[2] else 4

    w, pad = 1200, 40
    parts = []
    parts.append(f'<text x="{pad}" y="52" font-family="{SANS}" font-size="22" font-weight="700" fill="{TEXT}">Contribution activity</text>')
    parts.append(f'<text x="{w - pad}" y="52" text-anchor="end" font-family="{MONO}" font-size="13" fill="{DIM}">last 12 months</text>')
    parts.append(f'<rect x="{pad}" y="70" width="{w - 2 * pad}" height="1" fill="#FFFFFF" fill-opacity="0.07"/>')

    # Metric tiles
    by_month = {}
    for d in days:
        by_month[d["date"][:7]] = by_month.get(d["date"][:7], 0) + d["contributionCount"]
    best_key = max(by_month, key=by_month.get) if by_month else None
    best_n = by_month.get(best_key, 0)
    best_name = dt.date.fromisoformat(best_key + "-01").strftime("%b") if best_key else "-"
    plural = lambda n, word: f"{word}" if n == 1 else f"{word}s"
    # (value, unit, label, colour)
    metrics = [(f"{cal['totalContributions']:,}", "", "CONTRIBUTIONS", INDIGO),
               (f"{active}", plural(active, "day"), "ACTIVE DAYS", VIOLET),
               (f"{best_n}", f"in {best_name}", "BEST MONTH", CYAN),
               (f"{longest}", plural(longest, "day"), "LONGEST STREAK", PINK)]
    tile_w = (w - 2 * pad - 3 * 18) / 4
    for i, (val, unit, lab, col) in enumerate(metrics):
        x = pad + i * (tile_w + 18)
        parts.append(f'<rect x="{x:.1f}" y="92" width="{tile_w:.1f}" height="92" rx="14" fill="#FFFFFF" '
                     f'fill-opacity="0.03" stroke="#FFFFFF" stroke-opacity="0.07"/>')
        parts.append(f'<rect x="{x:.1f}" y="92" width="{tile_w:.1f}" height="3" rx="1.5" fill="{col}" fill-opacity="0.9"/>')
        parts.append(f'<text x="{x + 22:.1f}" y="146" font-family="{SANS}" font-size="38" font-weight="800" '
                     f'fill="{TEXT}">{esc(val)}</text>')
        if unit:
            vw = text_width(val, 38, bold=True)
            parts.append(f'<text x="{x + 34 + vw:.1f}" y="146" font-family="{SANS}" font-size="15" fill="{DIM}">{esc(unit)}</text>')
        parts.append(f'<text x="{x + 22:.1f}" y="170" font-family="{MONO}" font-size="11.5" letter-spacing="1.8" '
                     f'fill="{col}">{lab}</text>')

    # Heatmap
    cell, gap = 15.8, 4.3
    step = cell + gap
    gx, gy = pad + 46, 238
    months, last_m = [], None
    for wi, wk in enumerate(weeks):
        first = wk["contributionDays"][0]["date"]
        m = first[5:7]
        if m != last_m:
            months.append((wi, dt.date.fromisoformat(first).strftime("%b")))
            last_m = m
    for wi, name in months:
        if wi < len(weeks) - 2:
            parts.append(f'<text x="{gx + wi * step:.1f}" y="{gy - 12}" font-family="{MONO}" font-size="12" '
                         f'fill="{DIM}">{name}</text>')
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        parts.append(f'<text x="{gx - 12}" y="{gy + row * step + 11.5:.1f}" text-anchor="end" font-family="{MONO}" '
                     f'font-size="11.5" fill="{DIM}">{lab}</text>')
    n = len(weeks)
    for wi, wk in enumerate(weeks):
        col = []
        for d in wk["contributionDays"]:
            lv = level(d["contributionCount"])
            col.append(f'<rect x="{gx + wi * step:.1f}" y="{gy + d["weekday"] * step:.1f}" width="{cell}" '
                       f'height="{cell}" rx="3.5" fill="{HEAT[lv]}"/>')
        parts.append("".join(col))
    # A light sweep across the grid on load. It decorates the data; it never
    # hides it - a renderer that snapshots at t=0 still sees every cell.
    gw = n * step
    parts.append(f'<rect x="{gx - 120}" y="{gy - 4}" width="120" height="{7 * step + 4}" fill="url(#sweep)" opacity="0">'
                 f'<animate attributeName="x" values="{gx - 120};{gx + gw}" dur="2.4s" begin="0.3s" fill="freeze"/>'
                 f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.1;0.85;1" dur="2.4s" begin="0.3s" fill="freeze"/></rect>')

    # Legend
    ly = gy + 7 * step + 22
    parts.append(f'<text x="{gx}" y="{ly + 12}" font-family="{MONO}" font-size="12" fill="{DIM}">'
                 f'{cal["totalContributions"]:,} contributions · {u["repositories"]["totalCount"]} public repositories '
                 f'· on GitHub since {u["createdAt"][:4]}</text>')
    lx = w - pad - 5 * 19 - 46
    parts.append(f'<text x="{lx - 10}" y="{ly + 12}" text-anchor="end" font-family="{MONO}" font-size="12" fill="{DIM}">Less</text>')
    for i, c in enumerate(HEAT):
        parts.append(f'<rect x="{lx + i * 19}" y="{ly}" width="15" height="15" rx="3.5" fill="{c}"/>')
    parts.append(f'<text x="{lx + 5 * 19 + 6}" y="{ly + 12}" font-family="{MONO}" font-size="12" fill="{DIM}">More</text>')

    h = ly + 44
    sweep = ('<linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0">'
             '<stop offset="0" stop-color="#67E8F9" stop-opacity="0"/>'
             '<stop offset="0.5" stop-color="#67E8F9" stop-opacity="0.22"/>'
             '<stop offset="1" stop-color="#67E8F9" stop-opacity="0"/></linearGradient>')
    write("activity.svg", card(w, h, "".join(parts), label=f"{cal['totalContributions']} contributions in the last year",
                               extra_defs=sweep,
                               glows=((1150, 60, 220, INDIGO, 0.16), (60, h, 220, CYAN, 0.10))))


def build_languages(u):
    totals, colors = {}, {}
    counted = [r for r in u["repositories"]["nodes"] if r["name"] not in VENDORED]
    for repo in counted:
        for e in repo["languages"]["edges"]:
            name = e["node"]["name"]
            if name in ("Jupyter Notebook",):
                continue  # notebooks store outputs as bytes and drown everything else
            totals[name] = totals.get(name, 0) + e["size"]
            colors[name] = e["node"]["color"] or DIM
    total = sum(totals.values()) or 1
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    top, rest = ranked[:7], sum(v for _, v in ranked[7:])
    if rest:
        top.append(("Other", rest))
        colors["Other"] = "#475569"

    w, pad = 1200, 40
    parts = []
    parts.append(f'<text x="{pad}" y="52" font-family="{SANS}" font-size="22" font-weight="700" fill="{TEXT}">Languages</text>')
    parts.append(f'<text x="{w - pad}" y="52" text-anchor="end" font-family="{MONO}" font-size="13" fill="{DIM}">'
                 f'by bytes, across {len(counted)} repositories I wrote</text>')

    bar_w, bx, by = w - 2 * pad, pad, 82
    parts.append(f'<clipPath id="barclip"><rect x="{bx}" y="{by}" width="{bar_w}" height="14" rx="7"/></clipPath>')
    seg, x = [], bx
    for name, v in top:
        sw = bar_w * v / total
        seg.append(f'<rect x="{x:.2f}" y="{by}" width="{sw + 0.6:.2f}" height="14" fill="{colors[name]}"/>')
        x += sw
    parts.append(f'<g clip-path="url(#barclip)">{"".join(seg)}'
                 f'<rect x="{bx}" y="{by}" width="{bar_w}" height="14" fill="url(#barsheen)"/></g>')

    cols = 4
    colw = bar_w / cols
    for i, (name, v) in enumerate(top):
        x = bx + (i % cols) * colw
        y = 136 + (i // cols) * 34
        pct = v * 100 / total
        parts.append(f'<circle cx="{x + 7:.1f}" cy="{y - 5}" r="6" fill="{colors[name]}"/>')
        parts.append(f'<text x="{x + 22:.1f}" y="{y}" font-family="{SANS}" font-size="16" font-weight="600" fill="{TEXT}">{esc(name)}</text>')
        parts.append(f'<text x="{x + colw - 24:.1f}" y="{y}" text-anchor="end" font-family="{MONO}" font-size="14" '
                     f'fill="{DIM}">{pct:.1f}%</text>')
    h = 136 + ((len(top) - 1) // cols) * 34 + 34
    defs = ('<linearGradient id="barsheen" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#FFFFFF" stop-opacity="0.22"/>'
            '<stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>')
    write("languages.svg", card(w, h, "".join(parts), label="Languages by bytes", extra_defs=defs,
                                glows=((1100, h, 200, VIOLET, 0.12),)))


def build_footer():
    w, h = 1200, 76
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    inner = (
        f'<circle cx="38" cy="38" r="5" fill="{GREEN}"><animate attributeName="opacity" values="1;0.3;1" '
        f'dur="2.4s" repeatCount="indefinite"/></circle>'
        f'<text x="56" y="43" font-family="{MONO}" font-size="13.5" fill="{MUTED}">'
        f'Every card on this page is drawn by <tspan fill="{TEXT}">scripts/build.py</tspan> and refreshed nightly.</text>'
        f'<text x="{w - 36}" y="43" text-anchor="end" font-family="{MONO}" font-size="13" fill="{DIM}">built {esc(stamp)}</text>')
    write("footer.svg", card(w, h, inner, label="Footer", grid=False, rx=16,
                             glows=((40, 38, 80, GREEN, 0.18),)))


def main():
    print("static cards")
    build_about()
    build_work_nexaride()
    build_work_dendo()
    build_store_buttons()
    build_stack()
    for p in PROJECTS:
        build_project(p)
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
