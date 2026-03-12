# Personal Assistant

A Telegram-based daily recap assistant that:
- Messages you at 10pm with your Notion calendar events
- Has a natural back-and-forth conversation about each event
- Writes summaries back to your Notion event pages
- Updates your contact pages with interaction notes and follow-ups

## Stack
- Python + Flask (webhook server)
- Telegram Bot API (messaging)
- Supabase (conversation state)
- Claude API (AI conversation)
- Notion API (calendar + contacts)
- Railway (hosting)

## Setup

### 1. Environment Variables
Set these in Railway (never commit to GitHub):

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram user ID |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_KEY` | Your Supabase anon key |
| `NOTION_TOKEN` | Internal integration secret |
| `NOTION_CALENDAR_DB` | Calendar database ID |
| `NOTION_CONTACTS_DB` | Contacts database ID |
| `CLAUDE_API_KEY` | Anthropic API key |
| `TIMEZONE` | e.g. America/Los_Angeles |
| `TIMEZONE_OFFSET` | e.g. -7 for PT |

### 2. Deploy to Railway
1. Push this repo to GitHub
2. Create new project on railway.app
3. Connect your GitHub repo
4. Add all environment variables
5. Railway will auto-deploy

### 3. Register Telegram Webhook
After deploying, register your webhook by visiting:
```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://<YOUR_RAILWAY_URL>/webhook
```

### 4. Test
- Hit `POST /trigger-recap` to manually trigger the nightly recap
- Or wait for 10pm in your timezone

## Supabase Schema
The `sessions` table needs these columns:
- `id` (int8, primary key)
- `phone` (text) — stores Telegram chat ID
- `status` (text) — active | complete
- `events` (jsonb) — array of today's events
- `current_event_index` (int4)
- `conversation_history` (jsonb)
- `created_at` (timestamptz)
