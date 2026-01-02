#!/bin/bash
# Re-sign Dolphin.app with debug entitlements to allow memory access
# This is required for dolphin-memory-engine to work on macOS

set -e

DOLPHIN_APP="${1:-/Applications/Dolphin.app}"
DOLPHIN_BIN="$DOLPHIN_APP/Contents/MacOS/Dolphin"

if [ ! -f "$DOLPHIN_BIN" ]; then
    echo "Error: Dolphin not found at $DOLPHIN_APP"
    exit 1
fi

echo "Re-signing Dolphin with debug entitlements..."
echo "App: $DOLPHIN_APP"

# Create entitlements plist
ENTITLEMENTS=$(mktemp)
cat > "$ENTITLEMENTS" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.get-task-allow</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
</dict>
</plist>
PLIST

echo "Created entitlements file: $ENTITLEMENTS"
cat "$ENTITLEMENTS"

# Check current signature
echo ""
echo "Current signature:"
codesign -dvvv "$DOLPHIN_BIN" 2>&1 | head -5 || true

# Re-sign with entitlements (ad-hoc signing, no certificate needed)
echo ""
echo "Re-signing..."
codesign --force --deep --sign - --entitlements "$ENTITLEMENTS" "$DOLPHIN_APP"

# Verify
echo ""
echo "New signature:"
codesign -dvvv "$DOLPHIN_BIN" 2>&1 | head -10

echo ""
echo "Checking entitlements:"
codesign -d --entitlements :- "$DOLPHIN_BIN" 2>/dev/null || codesign -d --entitlements - "$DOLPHIN_BIN"

# Cleanup
rm -f "$ENTITLEMENTS"

echo ""
echo "Done! Dolphin has been re-signed with debug entitlements."
echo "You may need to restart Dolphin for changes to take effect."
