# ADR-009 — UI internationalization: English and Spanish

- **Status**: Superseded by [ADR-018](ADR-018-ui-i18n-as-built.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

The codebase, specs and documentation are in English ([P10](../../00-overview/constitution.md)), but the primary user works in Spanish and wants the UI in both languages. The domain contains user data in any language (task prompts, rubrics, model outputs, judge rationales) that must never be altered. Some messages originate below the UI (parameter validation, check details, run errors).

## Decision

1. UI strings live in flat JSON locale files `src/aidriven/ui/locales/en.json` and `es.json`, keys in dotted form (`models.params.temperature`, `review.pending_badge`). `en` is the reference: every key exists there.
2. A tiny `t(key, **params)` helper (`str.format` placeholders) resolves `current language → en → key`; a missing key is logged once at WARNING.
3. The language is a global setting `aidriven_settings/general.language` (`"en"` default, `"es"`), chosen in *Settings > General*; changing it reloads the current page.
4. **Domain and services never return UI sentences**: they return message keys with parameters (e.g. `{"key": "param.unsupported", "params": {"param": "temperature"}}`) and the UI translates them. Logs are always in English.
5. **User data is never translated**: task content, rulesets, agent personas, model responses, judge output, names.
6. Enum values are stored in English (`done`, `pending review` is `reviewed=false`) and translated only at display time (`status.done` → "Completado").
7. A unit test asserts that `es.json` has exactly the same key set as `en.json` and that placeholders match.

## Alternatives considered

- **gettext (.po/.mo)**: standard, but needs a compile step and tooling for a two-language UI.
- **Spanish-only UI**: contradicts the English-codebase rule and limits sharing.

## Consequences

- (+) Adding a language = adding one JSON file.
- (+) Domain stays language-neutral and testable.
- (−) Every user-visible message needs a key; the current `ModelProfile.validate_params` returns Spanish literals and must be migrated to keys.
