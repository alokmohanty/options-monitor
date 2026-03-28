# Discord Bot Setup Guide

This document walks you through creating and configuring the Discord bot for the options-monitor agent.

---

## 1. Create a Discord Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application**.
3. Give it a name (e.g., `Options Monitor`) and click **Create**.

---

## 2. Create the Bot User

1. In your application, click **Bot** in the left sidebar.
2. Click **Add Bot** → **Yes, do it!**
3. Under **Token**, click **Reset Token** and copy the token.
   > ⚠️ Keep this token secret. You will add it to your `.env` file as `DISCORD_BOT_TOKEN`.

---

## 3. Configure Bot Permissions

Under the **Bot** section, enable the following **Privileged Gateway Intents**:

| Intent | Required |
|--------|----------|
| `Message Content Intent` | ✅ Yes |
| `Server Members Intent` | Optional |
| `Presence Intent` | Optional |

---

## 4. Generate an Invite Link

1. Go to **OAuth2 → URL Generator** in the left sidebar.
2. Under **Scopes**, check:
   - `bot`
   - `applications.commands`
3. Under **Bot Permissions**, check:
   - `Send Messages`
   - `Read Message History`
   - `Use Slash Commands`
   - `Embed Links`
4. Copy the generated URL at the bottom of the page.

---

## 5. Invite the Bot to Your Server

1. Paste the URL into your browser.
2. Select the Discord server you want to add the bot to.
3. Click **Authorize**.

---

## 6. Create a Dedicated Channel (Recommended)

1. In your Discord server, create a text channel, e.g. `#bot-monitor`.
2. Copy the channel ID:
   - Enable **Developer Mode** in Discord: *User Settings → Advanced → Developer Mode*.
   - Right-click the channel → **Copy ID**.
3. Optionally add the channel ID to `config.yaml` under `discord.allowed_channel_ids` to restrict the bot to that channel only.

---

## 7. Add Credentials to `.env`

On your EC2 instance (or local machine for testing):

```bash
cp env.example .env
nano .env
```

Fill in:

```env
GEMINI_API_KEY=<your Gemini API key>
DISCORD_BOT_TOKEN=<the bot token from step 2>
```

---

## 8. Test the Bot

Once the bot is running (see `deployment.md`), open the Discord channel and try:

```
!help
!errors
!logs 100
!ask What trades were executed today?
!strategy
```

---

## Available Commands

| Command | Description |
|---------|-------------|
| `!ask <question>` | Ask anything about the trading bot |
| `!logs [n]` | Show last N lines of the log (default 50) |
| `!errors` | Scan log for errors and summarize |
| `!trades` | Show recent trade activity |
| `!strategy` | Explain the trading strategy from source code |
| `!reset` | Clear conversation history for the channel |
| `!help` | Show all available commands |
