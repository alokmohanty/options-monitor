# Deployment Guide — EC2

This document describes how to deploy the options-monitor agent on the same EC2 instance that runs the options trading bot.

---

## Prerequisites

- Ubuntu EC2 instance (the one running the trading bot at `/home/ubuntu/options-bot`).
- Python 3.12+ installed.
- [`uv`](https://docs.astral.sh/uv/) already installed.
- Your Gemini API key and Discord bot token ready.

---

## 1. Transfer the Project to EC2

From your local machine:

```bash
# Replace <EC2_IP> with your instance's public IP / hostname
# Replace <KEY.pem> with your SSH key file path
scp -r -i <KEY.pem> /path/to/options-monitor ubuntu@<EC2_IP>:~/options-monitor
```

Or use `git` if the project is in a repository:

```bash
ssh -i <KEY.pem> ubuntu@<EC2_IP>
git clone <your-repo-url> ~/options-monitor
```

---

## 2. Set Up Environment on EC2

SSH into the instance:

```bash
ssh -i <KEY.pem> ubuntu@<EC2_IP>
cd ~/options-monitor
```

Create and configure the `.env` file:

```bash
cp env.example .env
nano .env
```

Fill in:

```env
GEMINI_API_KEY=<your Gemini API key>
DISCORD_BOT_TOKEN=<your Discord bot token>
```

Install and sync dependencies:

```bash
uv sync
```

This creates a `.venv` virtual environment and installs all packages.

---

## 3. Test the Bot Manually

Run the bot once to verify everything works:

```bash
uv run options-monitor
```

You should see log output like:

```
2026-03-29 10:00:00 [INFO] Logged in as Options Monitor#1234 (ID: ...)
```

Test it in Discord by typing `!help` in the channel. Press `Ctrl+C` to stop.

---

## 4. Run as a systemd Service (Recommended)

Create a systemd service file so the bot starts automatically and restarts on failure.

```bash
sudo nano /etc/systemd/system/options-monitor.service
```

Paste the following (adjust paths if needed):

```ini
[Unit]
Description=Options Monitor Discord Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/options-monitor
ExecStart=/home/ubuntu/options-monitor/.venv/bin/python -m options_monitor.main
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable options-monitor
sudo systemctl start options-monitor
```

Check the status:

```bash
sudo systemctl status options-monitor
```

View live logs:

```bash
sudo journalctl -u options-monitor -f
```

---

## 5. Updating the Bot

When you push new code:

```bash
cd ~/options-monitor
git pull                 # or re-upload files
uv sync                  # re-install / update dependencies
sudo systemctl restart options-monitor
```

---

## 6. Environment Variable Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | ✅ | Google Gemini API key |
| `DISCORD_BOT_TOKEN` | ✅ | Discord bot token |

---

## 7. Configuration Reference (`config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `gemini.model` | `gemini-2.5-pro` | Gemini model to use |
| `gemini.temperature` | `1.0` | Response creativity (0–2) |
| `gemini.max_output_tokens` | `8192` | Max tokens per response |
| `discord.command_prefix` | `!` | Bot command prefix |
| `discord.allowed_channel_ids` | `[]` | Restrict to specific channels (empty = all) |
| `trading_bot.root_path` | `/home/ubuntu/options-bot` | Trading bot directory |
| `trading_bot.log_file` | `.../cron_output.log` | Path to the log file |
| `trading_bot.max_log_lines` | `500` | Max lines read per log request |

---

## 8. Troubleshooting

| Problem | Solution |
|---------|----------|
| `Missing required environment variables` | Check `.env` file exists and is filled |
| Bot doesn't respond | Verify `Message Content Intent` is enabled in Discord Developer Portal |
| `Log file not found` | Confirm `trading_bot.log_file` path in `config.yaml` matches the actual path |
| `Permission denied` reading bot code | Ensure the Ubuntu user has read access to `/home/ubuntu/options-bot` |
| Gemini API errors | Verify `GEMINI_API_KEY` is valid and has quota available |
