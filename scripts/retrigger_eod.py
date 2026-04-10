import sys
import json
import re
from pathlib import Path

# Add project root to path if running directly so 'options_monitor' is found
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from options_monitor import config
from options_monitor.scheduler import _read_log_for_date, _read_log_lines, _EOD_PROMPT, _gemini_one_shot, _format_eod_discord
from options_monitor.tools import save_journal_entry

def main():
    date_key = "2026-04-08"
    today_str = "08 April 2026"

    if len(sys.argv) > 1:
        date_key = sys.argv[1] # Expected YYYY-MM-DD
        from datetime import datetime
        try:
            dt = datetime.strptime(date_key, "%Y-%m-%d")
            today_str = dt.strftime("%d %B %Y")
        except Exception as e:
            print(f"Error parsing date {date_key}: {e}")
            sys.exit(1)

    print(f"Generating EOD summary for {today_str} ({date_key})...")
    
    log_content = _read_log_for_date(date_key)
    print(f"Log lines loaded: {len(log_content.splitlines())} lines for {date_key}")

    prompt = _EOD_PROMPT.format(date=today_str, date_key=date_key, log_content=log_content)
    
    print(f"Calling EOD model: {config.MonitorConfig.eod_model}")
    raw = _gemini_one_shot(prompt, max_tokens=4096, model=config.MonitorConfig.eod_model)
    
    try:
        clean = re.sub(r"^```[\w]*\n?", "", raw.strip(), flags=re.MULTILINE)
        clean = re.sub(r"```$", "", clean.strip())
        entry = json.loads(clean)
        
        save_journal_entry(date_key, entry)
        print("✅ Successfully generated JSON and appended to data/journal.json!")
        print("\n--- Discord format preview ---")
        print(_format_eod_discord(entry, today_str))
        
    except (json.JSONDecodeError, Exception) as parse_err:
        print("❌ Parse failed:", parse_err)
        print("\nRaw API output was:")
        print(raw)

if __name__ == "__main__":
    main()
