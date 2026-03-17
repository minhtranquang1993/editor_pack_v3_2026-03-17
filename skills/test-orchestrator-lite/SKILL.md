---
name: test-orchestrator-lite
description: >-
  Điều phối test suite cho các skill và tool. Chạy smoke test, integration test,
  và báo cáo kết quả. Lightweight version không cần external test runner.
---

# test-orchestrator-lite

## Trigger

- Manual: `/test` hoặc "chạy test"
- Auto: Sau khi deploy skill mới

## Workflow

1. Scan skills/ directory cho available tests
2. Chạy smoke tests (SKILL.md validation, script compilation)
3. Chạy integration tests nếu có
4. Generate test report → `memory/test-report.md`

## Notes

This skill orchestrates existing test tools (`tools/skill_smoke_test.py`,
`tools/skill_health.py`) rather than providing its own test scripts.
It follows an instruction-based workflow to coordinate test execution.

## Category
devops

## Risk
low
