# OneBot 11 Config (NapCat Linux)

**File**: `/home/ji/.napcat/config/onebot11_{{BOT_QQ_ID}}.json`

```json
{
  "network": {
    "httpServers": [
      {
        "enable": true,
        "name": "http",
        "host": "127.0.0.1",
        "port": 3000,
        "token": "***",
        "debug": true
      }
    ],
    "httpSseServers": [],
    "httpClients": [],
    "websocketServers": [
      {
        "enable": true,
        "name": "1",
        "host": "127.0.0.1",
        "port": 3001,
        "reportSelfMessage": false,
        "enableForcePushEvent": true,
        "messagePostFormat": "array",
        "token": "***",
        "debug": true,
        "heartInterval": 30000
      }
    ],
    "websocketClients": [],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false,
  "imageDownloadProxy": "",
  "timeout": {
    "baseTimeout": 10000,
    "uploadSpeedKBps": 256,
    "downloadSpeedKBps": 256,
    "maxTimeout": 1800000
  }
}
```

## NapCat Core Config

**File**: `/home/ji/.napcat/config/napcat_{{BOT_QQ_ID}}.json`

```json
{
  "fileLog": false,
  "consoleLog": true,
  "fileLogLevel": "debug",
  "consoleLogLevel": "info",
  "packetBackend": "auto",
  "packetServer": "",
  "o3HookMode": 1,
  "bypass": {
    "hook": false,
    "window": false,
    "module": false,
    "process": false,
    "container": false,
    "js": false
  },
  "autoTimeSync": true
}
```

## WebUI Config

**File**: `/home/ji/.napcat/config/webui.json`

- URL: `http://127.0.0.1:6099/webui`
- Token is printed in startup log
