---
name: ads-insight-auto
description: >-
  Tự động phân tích data ads (FB/TikTok/Google) từ Supabase, so sánh với baseline 7 ngày trước,
  đưa ra insight ngắn gọn + khuyến nghị action cụ thể. Alert qua Telegram khi phát hiện anomaly.
  Trigger: cron 19h hằng ngày hoặc manual /ads-insight.
---

# ads-insight-auto

## Workflow

### 1. Data Collection
- Source: Supabase table `daily_ads_report`
- Fields: report_date, platform, branch, cost, reach, impressions, clicks, cpm, cpc, messaging, leads
- Scope: hôm nay + 7 ngày trước (tất cả platforms + branches)

### 2. Baseline Calculation
- 7-day average: avg CPL, CTR, CPC, CPM, spend cho mỗi platform
- Yesterday comparison: hôm nay vs hôm qua
- Week trend: xu hướng 7 ngày

### 3. Anomaly Detection
Áp dụng rules từ `references/insight-rules.md`:
- CPL deviation > 20%
- CTR deviation > 15%
- Spend vs daily budget > 120%
- Zero leads sau 12h chạy
- Platform efficiency ranking

### 4. Insight Generation — Output Format
```
📊 ADS INSIGHT — {DATE}

[SUMMARY]
• FB: Spend {X}M | Lead {N} | CPL {X}K (↑{%} vs avg)
• TikTok: Spend {X}M | Lead {N} | CPL {X}K (↓{%} vs avg)
• Google: Spend {X}M | Lead {N} | CPL {X}K (→ stable)

[ALERTS]
⚠️ {PLATFORM} CPL spike +{%} → creative fatigue?
🔴 CRITICAL: {PLATFORM} lead = 0 sau 12h

[RECOMMENDATION]
✅ Platform hiệu quả nhất: {PLATFORM} (CPL {X}K)
→ Tăng budget {PLATFORM} thêm 20%
→ Pause creative {ID} (low CTR)
→ Test creative mới (A/B test)

[NEXT ACTIONS]
1. Pause: {creative_id} (CTR {X}% vs avg {Y}%)
2. Launch: {platform} campaign variant (budget +{X}K)
3. Review: LP bounce rate
```

### 5. Telegram Alert
- Target: chat_id từ credentials/telegram_token.txt
- Frequency: 1 tin/ngày (không spam)
- Format: Markdown + emoji

### 6. Storage
- Insight: `memory/insights/{YYYY-MM-DD}-ads.md`
- Anomalies log: `memory/anomalies.log` (CSV)

## Input Parameters
```json
{
  "date": "YYYY-MM-DD",
  "platforms": ["fb", "tiktok", "google"],
  "branches": ["all"]
}
```

## Error Handling
- Supabase timeout → retry 3x → alert "Data unavailable"
- Zero leads → immediate alert (không chờ daily schedule)
- Missing data → fallback yesterday data

## Dependencies
- supabase-py, python-telegram-bot, pandas
- Credentials: credentials/supabase_url.txt, credentials/supabase_key.txt, credentials/telegram_token.txt

## Related Skills
- report-ads, ads-anomaly, ads-budget-pacing, creative-fatigue-detector

## Category
marketing-ads

## Scripts

Hands-based script — chạy qua `run_hand()`, không có CLI args riêng.

```bash
python3 skills/ads-insight-auto/scripts/ads_insight.py
```

Workflow tự động:
1. Load secrets từ `credentials/report_ads_secrets.json`
2. Query Supabase `daily_ads_report` (8 ngày gần nhất)
3. Aggregate by platform → calc baseline 7-day avg
4. Apply anomaly rules (CPL spike, CTR drop, zero leads, overspend)
5. Build insight message → gửi Telegram
6. Save insight → `memory/insights/{YYYY-MM-DD}-ads.md`
