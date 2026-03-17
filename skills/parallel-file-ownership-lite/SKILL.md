---
name: parallel-file-ownership-lite
description: >-
  Theo dõi ownership của files trong workspace, tránh conflict khi nhiều agent
  cùng chỉnh sửa. Lightweight version chạy in-memory.
---

# parallel-file-ownership-lite

## Trigger

- Auto: Khi agent bắt đầu edit file
- Manual: `/file-ownership` hoặc "kiểm tra file lock"

## Workflow

1. Check file lock status trước khi edit
2. Acquire lock khi bắt đầu chỉnh sửa
3. Release lock sau khi hoàn tất
4. Alert nếu conflict detected

## Notes

This skill operates as an instruction-based workflow — the agent follows the
ownership protocol described above when editing shared files. No separate CLI
tool is required; the workspace tools handle file operations directly.

## Category
agent-core

## Risk
low
