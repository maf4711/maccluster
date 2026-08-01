#!/bin/bash
# Double-click or run in Terminal — prompts for admin password, then up.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
osascript -e 'do shell script "'$(command -v maccluster)' up" with administrator privileges'
echo "---"
maccluster doctor
maccluster status
read -n 1 -s -r -p "Press any key..."
