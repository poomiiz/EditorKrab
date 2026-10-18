# UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every page in `app/static` one calm, on-brand look and fix the copy and flow problems from the 24-point audit, starting with a reviewed pilot of `video-editor.html`.

**Architecture:** Shared design tokens live in `shell.css` (already loaded by every app page). Each page drops its own hard-coded colors in favour of those tokens, then gets a copy/flow pass. No ids, classes or `onclick` targets that JS reads are renamed; markup changes are additive or text-only.

**Tech Stack:** Vanilla HTML/CSS/JS served by FastAPI (`app/app.py`); i18n through `ui.js` + `app/static/i18n/en.json`.

**Spec:** `docs/PRODUCT.md` (users, principles, anti-references) and the owner's order of 2026-10-18 (redesign every page with impeccable; pilot `video-editor.html` first and wait for review).

## Global Constraints

- Brand palette from `logo.svg`: navy `#17243B`, coral `#FF785C`, cream `#F7F5EF`. Accent is coral; text on coral is navy, never white (contrast).
- Dark theme stays: editors judge footage colour on it for hours.
- Every new or changed Thai string has an `en.json` entry; `python scripts/i18n_coverage_test.py` must pass.
- One name for the helper: "AI" in Thai and English copy. Product names (Claude Code, Codex) only where the person must choose which tool runs.
- No em dashes in UI copy. No emoji as icons in new markup; existing emoji labels on this page are replaced by text or the shell's SVG icon style.
- Never `git add -A` / `git add .`; stage explicit paths. Main checkout has 56 `.md` symlinks to an Obsidian vault: do not touch.
- Scripts raise on bad input, no silent fallback (AGENTS.md).

## Review Focus

- Thai and English both render: switching TH/EN on a changed page shows no Thai left in English mode (coverage test + manual switch).
- Keyboard: Tab reaches every control with a visible focus ring on the dark theme.
- Long Thai project names and file paths do not overflow cards (ellipsis + title attribute).
- Team review link appears only for people with review access (`/api/team/...` answer), and the page still works when that call fails.
- Narrow window (760 px and below) keeps the bottom tab bar usable.

---

### Task 1: Shared tokens and base controls (`shell.css`)

**Files:** Modify `app/static/shell.css`

- [ ] Add `:root` tokens: `--ek-bg`, `--ek-surface`, `--ek-surface-2`, `--ek-line`, `--ek-text`, `--ek-text-2`, `--ek-muted` (AA on `--ek-surface`), `--ek-accent` (coral), `--ek-accent-ink` (navy), `--ek-ok`, `--ek-warn`, `--ek-danger`, `--ek-focus`. Point `--brand` at `--ek-accent`.
- [ ] Global `:focus-visible` ring using `--ek-focus`; `prefers-reduced-motion` turns off transforms/transitions.
- [ ] Sidebar create button: coral with navy text.
- [ ] Verify: `node scripts/ui_lang_test.js`, page screenshot at 1320x860.

### Task 2: Pilot `video-editor.html` (owner review gate)

**Files:** Modify `app/static/video-editor.html`, `app/static/i18n/en.json`

- [ ] Map page `:root` variables onto the shell tokens; replace hard-coded colours with tokens.
- [ ] Remove the decorative blur, gradient hero and glow; one primary action per screen.
- [ ] Copy pass from the audit: every hint names a button that exists, one helper term, errors say what to do next, no raw JSON asks.
- [ ] Hide the team review link unless the person has review access.
- [ ] Icon-only controls get `aria-label`.
- [ ] Verify: i18n coverage test, all `scripts/*_test.js|cjs`, related python tests, screenshots before/after at 1320x860 and 760x900.
- [ ] Commit, push `claude/ui-redesign`, open draft PR, send screenshots to the owner. **Stop until the owner approves.**

### Task 3..N: Remaining pages, one task each, same checklist as Task 2

`editor.html`, `setup.html`, `settings.html`, `styles.html`, `motion.html`, `cover.html`, `library.html`, `team.html`, `review.html`, `login.html`, shell (`shell.js` sidebar labels). Each: tokens, copy pass, a11y names, screenshots, tests, commit.
