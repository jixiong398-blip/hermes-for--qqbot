# QR Code Login Workflows

## The Fundamental Constraint

QQ's "扫一扫" feature does NOT allow scanning QR codes from the phone's photo album. Camera-only. This means:
- You cannot save a QR image to phone → open in QQ → it scans from album. It won't work.
- You MUST display the QR code on a physical or virtual screen that the camera can see.

## Workflow A — Second Device Display
1. Capture QR code from NapCat: it's saved to `cache/qrcode.png` after ~8s of startup
2. Send image to user via Feishu: `send_message(target="feishu:oc_...", message="MEDIA:/path/to/qrcode.png\n说明")`
3. User displays the image on a second phone/tablet/computer screen
4. User opens QQ on their main phone, uses "扫一扫" camera to scan the second screen

## Workflow B — Decoded URL
1. Read the line `二维码解码URL: https://txz.qq.com/p?k=<...>&f=1600001615` from `/tmp/napcat.log`
2. Send this URL to the user
3. User opens the URL on their phone — QQ app intercepts and shows authorization page

## Workflow C — WebUI
1. NapCat WebUI runs on `http://<server-ip>:6099`
2. Token is in `webui.json` key `"token"` or printed in startup log
3. Full URL: `http://<server-ip>:6099/webui?token=<token>`
4. WebUI provides a QR code display and password login option
5. User opens this URL in phone browser → scans the QR shown on the WebUI page

## Workflow D — Password Login (no QR needed)
1. Set env vars before starting: `NAPCAT_QUICK_PASSWORD` and/or `NAPCAT_QUICK_PASSWORD_MD5`
2. NapCat will attempt quick login first, falling back to QR only if password is wrong/unset
3. This is the most reliable method for a headless server

## SMS Verification Dead End

When using password login, QQ may require SMS/captcha verification. The flow:
1. NapCat prints a `proofWaterUrl` — a OneClick link like `https://ti.qq.com/safe/tools/captcha/sms-verify-login?...`
2. This link is designed for mobile QQ app to intercept, but **it does not work reliably** on most phones
3. The user opens the link → browser tries to jump to QQ app → QQ app fails to complete the flow → dead end
4. NapCat log says "请在 WebUi 中继续完成验证" — WebUI is the only reliable path for completing SMS verification
5. If you restart NapCat between verification attempts, the session SID changes and the user must verify again

## Log Indicators
- `正在快速登录 {{BOT_QQ_ID}}` → attempting password login
- `快速登录错误：登录态已失效` → session expired, need fresh login
- `将使用二维码登录方式` → falling back to QR
- `二维码已保存到 ... qrcode.png` → QR image is ready
- `请扫描下面的二维码，然后在手Q上授权登录` → user action needed
- `检测到 NAPCAT_QUICK_PASSWORD` → password env var was picked up
- `需要验证码, proofWaterUrl:` → SMS verification required
- `密码回退需要验证码，请在 WebUi 中继续完成验证` → complete via WebUI
