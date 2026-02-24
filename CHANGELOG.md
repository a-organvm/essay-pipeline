# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-02-24

### Added

- `src/topic_suggester.py` — analyzes corpus for under-covered tags, underserved categories, surfaced articles, and orphan essays to generate essay topic suggestions (`essay-topic-suggestions` produce edge)
- `src/sprint_narrator.py` — combines analytics metrics, GitHub activity, essay stats, and publication cadence into a markdown sprint narrative (`sprint-narrative-draft` produce edge)
- Test suites for both new modules (~40 tests)
- Test fixtures: mini JSON datasets for topic suggester and sprint narrator
- CLI entry points: `essay-suggest` and `essay-narrate`
- Ruff linting in CI workflow

### Changed

- Bumped version to 0.2.0
- Updated pyproject.toml description to reflect expanded capabilities

## [0.1.0] - 2026-02-17

### Added

- Initial creation as part of ORGAN-V LOGOS Infrastructure Campaign
- Core project structure and documentation
- README with portfolio-quality documentation

[Unreleased]: https://github.com/organvm-v-logos/essay-pipeline/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/organvm-v-logos/essay-pipeline/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/organvm-v-logos/essay-pipeline/releases/tag/v0.1.0
