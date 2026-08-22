#!/usr/bin/env bash
# ==============================================================================
# Dynamic Launchd / Cron Setup for Social Listening Loop
# ==============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON_EXE="$(which python3)"
BASH_EXE="$(which bash)"

echo "=== Social Listening Scheduler Setup ==="
echo "Project Directory: $HERE"
echo "Python Executable: $PYTHON_EXE"
echo ""

if [[ "$OSTYPE" == "darwin"* ]]; then
  echo "Detected macOS. Installing launchd plists to ~/Library/LaunchAgents..."
  mkdir -p "$HOME/Library/LaunchAgents"

  # Digest plist (every 3h = 10800s)
  DIGEST_PLIST="$HOME/Library/LaunchAgents/com.social-listening.digest.plist"
  cat <<EOF > "$DIGEST_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.social-listening.digest</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:$HOME/.npm-global/bin:$PATH</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>ProgramArguments</key><array>
    <string>$BASH_EXE</string>
    <string>$HERE/run.sh</string>
  </array>
  <key>StartInterval</key><integer>10800</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$HERE/data/launchd.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/launchd.log</string>
</dict>
</plist>
EOF

  # Daily plist (21:00)
  DAILY_PLIST="$HOME/Library/LaunchAgents/com.social-listening.daily.plist"
  cat <<EOF > "$DAILY_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.social-listening.daily</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:$HOME/.npm-global/bin:$PATH</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>ProgramArguments</key><array>
    <string>$PYTHON_EXE</string>
    <string>$HERE/daily.py</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>21</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$HERE/data/daily.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/daily.log</string>
</dict>
</plist>
EOF

  launchctl unload "$DIGEST_PLIST" 2>/dev/null || true
  launchctl unload "$DAILY_PLIST" 2>/dev/null || true
  launchctl load "$DIGEST_PLIST"
  launchctl load "$DAILY_PLIST"
  echo "✅ Launchd agents loaded successfully!"
  echo "Check logs with: tail -f $HERE/data/launchd.log"

else
  echo "Detected Linux / Unix. Add the following to your crontab (crontab -e):"
  echo ""
  echo "0 */3 * * * $BASH_EXE $HERE/run.sh >> $HERE/data/cron.log 2>&1"
  echo "0 21 * * * $PYTHON_EXE $HERE/daily.py >> $HERE/data/daily.log 2>&1"
  echo ""
fi
