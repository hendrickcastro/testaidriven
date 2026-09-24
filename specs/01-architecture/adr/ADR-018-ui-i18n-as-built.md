# ADR-018 — UI internationalization, English and Spanish (as built)

- **Status**: Accepted — §1 (where strings are edited) superseded by [ADR-023](ADR-023-generated-locales-and-theme.md)
- **Date**: 2026-09-24
- **Supersedes**: [ADR-009](ADR-009-ui-i18n-en-es.md)

## Context

ADR-009 required that domain and services return message keys instead of sentences, that missing keys be logged, and that the language live in `aidriven_settings/general`. The implementation translates the whole UI chrome but keeps technical messages from lower layers in English, and stores the language in the local configuration.

## Decision

1. UI strings live in flat JSON files `src/aidriven/ui/locales/en.json` and `es.json` (471 keys each at the time of writing), dotted keys grouped by area (`nav.*`, `models`/`providers.*`, `profiles.*`, `tasks.*`, `runs.*`, `rerun.*`, `results.*`, `compare.*`, `settings.*`, `status.*`, `dim.*`, `cap.*`…).
2. `t(key, **params)` (`ui/i18n.py`) resolves *current language → English → the key itself* and applies `str.format` placeholders (formatting errors return the unformatted text). Missing keys are not logged; the test suite prevents them.
3. The language is `AppConfig.language` (`data/config.json`, default `en`) and is mirrored per browser in `app.storage.user["lang"]`. It can be changed from the **header selector** or *Settings > General*; both update the config and reload the page.
4. **Technical messages from domain and services are English and shown verbatim** (e.g. `validate_params()` problems, run/result errors, check details, notifications built from exceptions). Logs are always English.
5. **User data is never translated**: task content, rulesets, personas, model outputs, judge rationales, names.
6. Enum values are stored in English and translated at display time through key families (`status.<value>`, `reasoning.<mode>`, `tool.<name>`, `difficulty.*`, `source.*`, `cap.*`, `score.*`, `dim.*`, `rerun.scope_*`, `rerun.mode_*`, `providers.keysrc_*`).
7. `tests/unit/test_i18n.py` asserts that every key used by `ui/` (literal `t("…")` calls, tuple keys and the dynamic families above) exists in **both** locales, and that `en.json` and `es.json` have the same key set and the same placeholders per key.

## Alternatives considered

- **Message keys from services** (ADR-009): fully localized errors, but every validation and error path would need a key and parameters; deferred.
- **gettext**: compile step and tooling for two languages.

## Consequences

- (+) Adding a language = one JSON file (plus its entry in `LANGUAGES`).
- (+) Parity between locales is enforced by CI.
- (−) With the UI in Spanish, validation problems and error details still appear in English.
