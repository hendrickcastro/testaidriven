# ADR-023 — Locale files generated from `tools/gen_locales.py`; light/dark theme

- **Status**: Accepted
- **Date**: 2026-09-24
- **Supersedes**: [ADR-018](ADR-018-ui-i18n-as-built.md) §1 (where UI strings are edited)

## Context

With 500+ UI strings, editing `en.json` and `es.json` separately made it easy to add a key to one locale only (caught by the i18n test, but late). The UI also needed a dark mode: several light-only Quasar colour classes (`bg-grey-1`, `text-grey-9`…) and chart colours were unreadable on a dark background.

## Decision

1. **`tools/gen_locales.py` is the source of truth** for UI strings: one dict `key → (en, es)`; running `python tools/gen_locales.py` regenerates `src/aidriven/ui/locales/en.json` and `es.json` (514 keys each on 2026-09-24). The JSON files are generated artifacts and are not edited by hand; `t()` and the parity test (ADR-018 §2, §7) are unchanged.
2. **Theme**: a header button cycles `auto` → `light` → `dark` (`ui/common.py` `THEMES` → `ui.dark_mode(None | False | True)`, icons per theme, tooltip `theme.<name>`). The choice is stored per browser (`app.storage.user["theme"]`) and in `AppConfig.theme` (default `auto` = follow the OS).
3. Light-only colour classes are replaced by Tailwind classes with `dark:` variants; charts (`ui/charts.py`) use neutral text/axis colours and a transparent background so they work on both themes.

## Consequences

- (+) Adding a string is one line with both languages side by side.
- (+) The UI is usable in dark mode, including charts.
- (−) Editing a JSON locale by hand is overwritten by the next generation.
