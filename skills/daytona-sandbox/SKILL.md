---
name: daytona-sandbox
description: >-
  Quản lý Daytona sandbox cho code execution an toàn.
  Tạo, chạy, cleanup sandbox environments tự động.
---

# daytona-sandbox

## Trigger

- Auto: Khi cần chạy code trong sandbox environment
- Manual: `/daytona` hoặc "tạo sandbox"

## Workflow

1. Request sandbox từ Daytona API
2. Upload code/script vào sandbox
3. Execute và capture output
4. Cleanup sandbox sau khi xong

## Category
infrastructure

## Risk
medium
