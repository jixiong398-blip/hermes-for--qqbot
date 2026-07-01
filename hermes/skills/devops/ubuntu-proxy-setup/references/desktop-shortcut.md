# Desktop Shortcut for v2rayA Web Panel

Creates a clickable desktop icon to open the v2rayA management panel at `http://localhost:2017`.

## Desktop File

```bash
DESKTOP_DIR=$(xdg-user-dir DESKTOP)  # ~/桌面 or ~/Desktop
cat > "$DESKTOP_DIR/v2rayA面板.desktop" << 'EOF'
[Desktop Entry]
Name=v2rayA 代理面板
Comment=打开 v2rayA Web 管理面板
Exec=xdg-open http://localhost:2017
Icon=network-server
Terminal=false
Type=Application
Categories=Network;
StartupNotify=true
EOF

chmod +x "$DESKTOP_DIR/v2rayA面板.desktop"
```

## Notes

- On GNOME, first double-click may show a security dialog — click **Trust and Launch** to allow
- If the desktop icon shows as a generic gear/file icon instead of a network icon, this is cosmetic — the `network-server` icon may not be available on all themes
- The panel will open in the user's default browser (Firefox, Chrome, etc.), not embedded
- Login credentials: `admin` / saved password (check vault or ask user to re-enter)
