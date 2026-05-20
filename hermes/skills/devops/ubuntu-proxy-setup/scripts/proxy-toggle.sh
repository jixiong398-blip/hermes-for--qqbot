#!/bin/bash
# proxy management command for v2rayA
# Source this file in .bashrc: source /home/ji/proxy.sh

proxy() {
    case "$1" in
        on)
            gsettings set org.gnome.system.proxy mode 'manual'
            gsettings set org.gnome.system.proxy.http host ''
            gsettings set org.gnome.system.proxy.http port 0
            gsettings set org.gnome.system.proxy.https host ''
            gsettings set org.gnome.system.proxy.https port 0
            gsettings set org.gnome.system.proxy.socks host '127.0.0.1'
            gsettings set org.gnome.system.proxy.socks port 20170
            echo "System proxy ON (SOCKS5 127.0.0.1:20170)"
            ;;
        off)
            gsettings set org.gnome.system.proxy mode 'none'
            echo "System proxy OFF"
            ;;
        status)
            mode=$(gsettings get org.gnome.system.proxy mode)
            if [ "$mode" = "'manual'" ]; then
                echo "Proxy: ON"
                echo "  SOCKS5: $(gsettings get org.gnome.system.proxy.socks host):$(gsettings get org.gnome.system.proxy.socks port)"
            else
                echo "Proxy: OFF"
            fi
            if curl -s --socks5 127.0.0.1:20170 -o /dev/null -w "" https://github.com --connect-timeout 5 --max-time 10 >/dev/null 2>&1; then
                echo "GitHub: accessible via proxy"
            else
                echo "GitHub: not accessible"
            fi
            ;;
        web)
            xdg-open http://localhost:2017 >/dev/null 2>&1
            echo "Opening v2rayA panel..."
            ;;
        *)
            echo "Usage: proxy {on|off|status|web}"
            ;;
    esac
}
