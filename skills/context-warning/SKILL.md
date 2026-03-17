---
name: context-warning
description: >-
  Cảnh báo context window sắp đầy, tự động tóm tắt và chuyển session khi cần.
  Giúp agent tránh mất thông tin quan trọng khi context dài.
---

# context-warning

## Trigger

- Auto: Khi phát hiện context window > 80% capacity
- Manual: `/context-warning` hoặc "kiểm tra context"

## Workflow

1. Monitor context window size
2. Khi gần đầy → tóm tắt conversation hiện tại
3. Lưu summary vào memory
4. Alert user nếu cần chuyển session

## Category
agent-core

## Risk
low
