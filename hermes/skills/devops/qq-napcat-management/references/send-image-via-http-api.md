# 通过 OneBot HTTP API 发送图片

## 背景

虽然 `send_message` 的 `MEDIA:` 前缀在 OneBot 上不支持，但可以直接调用 NapCat 的 HTTP API 发送图片。

## 完整命令

```bash
curl -X POST "http://127.0.0.1:3000/send_msg" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {{ONEBOT_TOKEN}}" \
  -d '{
    "message_type": "private",
    "user_id": {{HOME_CHANNEL}},
    "message": [
      {
        "type": "image",
        "data": {
          "file": "file:///path/to/image.jpg"
        }
      }
    ]
  }'
```

## 参数说明

| 字段 | 说明 | 示例值 |
|------|------|--------|
| `message_type` | 消息类型 | `"private"` 私聊 / `"group"` 群聊 |
| `user_id` | 用户 QQ 号（私聊用） | `{{HOME_CHANNEL}}` |
| `group_id` | 群号（群聊用） | `796091804` |
| `message[0].type` | 消息段类型 | `"image"` |
| `message[0].data.file` | 图片路径 | `"file:///home/ji/Pictures/xxx.jpg"` |

## 群聊发图

```bash
curl -X POST "http://127.0.0.1:3000/send_msg" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer {{ONEBOT_TOKEN}}" \
  -d '{
    "message_type": "group",
    "group_id": 796091804,
    "message": [{"type": "image", "data": {"file": "file:///home/ji/Pictures/xxx.jpg"}}]
  }'
```

## 已验证 (2026-05-19)

- 私聊发送 soyo chibi tea 图片 → `retcode: 0`, `message_id: 643975527`
- `file://` 路径方式正常工作，图片在 QQ 客户端正常显示
- NapCat 自动处理图片上传和格式转换

## 注意事项

- 图片路径必须是绝对路径
- 支持的图片格式：jpg, png, gif 等常见格式
- 大图片 NapCat 会自动压缩
- `Authorization` header 使用 OneBot 配置中的 access token
