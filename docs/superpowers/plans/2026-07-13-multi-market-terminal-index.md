# Multi-Market Industrial Terminal Execution Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute these plans in order. Do not use subagents unless the user explicitly authorizes delegation.

**Goal:** Deliver the approved multi-market industrial terminal while preserving the existing A-share warehouse and leaving every change uncommitted for user review.

**Architecture:** Four dependent plans separate platform foundations, domain data, equity workflows, and operations/UI closure. Each plan produces independently testable software and must finish with a clean verification checkpoint before the next plan starts.

**Tech Stack:** Python 3, Flask, SQLite WAL, pandas, Tushare, vanilla JavaScript, ECharts, CSS, pytest, pywebview.

## Global Constraints

- Baseline is Git commit `a45a43f`; recheck HEAD before execution.
- Do not commit, stage, push, or create a pull request.
- Preserve the current A-share `data/providers/<provider>/` warehouses.
- Do not package market data, caches, logs, generated outputs, or credentials.
- Use `.venv/bin/python` and `.venv/bin/pytest`.
- Use TDD for every production behavior change.
- Use `#000000` as the primary canvas, `#FF6900` for actions, `#FF3131` for vivid red semantics, and `#00FF41` for vivid green semantics.
- A-share market policy must never run silently against Hong Kong instruments.
- Live provider checks must use bounded temporary stores; do not run a full production-market sync solely for verification.
- Update `AGENTS.md` only after behavior is verified.

## Execution Order

1. [Platform Foundation](2026-07-13-multi-market-01-platform-foundation.md)
2. [Domain Data](2026-07-13-multi-market-02-domain-data.md)
3. [Equity Workspace](2026-07-13-multi-market-03-equity-workspace.md)
4. [Operations, UI, and Verification](2026-07-13-multi-market-04-ops-ui-verification.md)

Do not start a later plan while an earlier plan has failing focused tests.

