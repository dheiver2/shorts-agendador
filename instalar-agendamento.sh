#!/bin/bash
# Roda o agente a cada 2 horas e ao fazer login (se o Mac estiver dormindo, roda ao acordar).
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/br.mangaba.shorts-agendador.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>br.mangaba.shorts-agendador</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>$DIR/agente.py</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StartInterval</key><integer>7200</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$DIR/launchd.log</string>
</dict></plist>
PL
launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST"
echo "Agendamento instalado: $PLIST"

# Instagram: a API não agenda, então publica de hora em hora, 1 min depois do horário do YouTube (07:01–21:01).
PLIST_IG="$HOME/Library/LaunchAgents/br.mangaba.shorts-instagram.plist"
HORAS=""
for h in $(seq 7 21); do HORAS="$HORAS<dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>1</integer></dict>"; done
cat > "$PLIST_IG" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>br.mangaba.shorts-instagram</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>$DIR/agente.py</string><string>espelhos</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key><array>$HORAS</array>
  <key>StandardOutPath</key><string>$DIR/launchd.log</string>
  <key>StandardErrorPath</key><string>$DIR/launchd.log</string>
</dict></plist>
PL
launchctl bootout gui/$(id -u) "$PLIST_IG" 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST_IG"
echo "Agendamento do Instagram instalado: $PLIST_IG"

