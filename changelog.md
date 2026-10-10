# Changelog

All notable changes to `agy-client` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Visual walkthrough demos in `README.md` featuring `screenshot1.png` (piping structured data via `agy_client`) and `screenshot2.png` (interactive desktop region capture via `agy-screenshot`).
- Descriptive step-by-step captions detailing the execution flow (right-pane CLI queuing first, followed by left-pane Antigravity ingestion and response).
- Local repository `changelog.md` tracking project development history.

## [0.1.0] - 2026-10-10

### Added
- Core `agy_client` CLI entry point (`bin/agy_client`) supporting standard input piping, direct prompts, and clipboard inspection (`-c`/`--clipboard`).
- Standalone `agy-screenshot` utility (`bin/agy-screenshot`) for interactive screen area selection, window capture, and clipboard image delivery.
- File-based zero-network architecture using atomic session inboxes (`~/.gemini/antigravity-cli/agy_client/inbox/<cid>/`).
- Native kernel lock discovery probing Antigravity presence locks (`~/.gemini/antigravity-cli/presence/*.lock`) using non-blocking POSIX `fcntl.flock`.
- Antigravity lifecycle plugin integration via `hooks.json` and `scripts/session_sync.py` (`PreInvocation` hook) with dynamic `injectSteps` injection.
- Smart session routing with 3-tier fallback (explicit ID/index, workspace path matching, recency fallback).
- Built-in behavioral rules (`rules/AGENTS.md`) and skill definitions (`skills/piped-context/SKILL.md`).
- Automated installer script `install.sh` with PATH configuration checks and automated symlinking.
- Unit test suite in `tests/test_client.py`.
