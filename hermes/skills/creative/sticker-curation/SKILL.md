---
name: sticker-curation
description: "How and when to save interesting images from group chat as your own stickers. Pick funny/cute/spot-on reaction images, label them by emotion, then reuse via [sticker:emotion]."
version: 1.0
author: hermes-agent
category: creative
metadata:
  hermes:
    tags: [stickers, curation, emotion, expression, soyo, image-collection]
    related_skills: [chat-cadence]
---

# Sticker Curation — 自己挑表情包

把群里看到的、让人眼前一亮的图收下来归类，方便以后自己用 `[sticker:情绪名]` 调出来。像真的人存表情包那个感觉——不是搜集每张图，是搜集**自己也想用的那张**。

## 收集准则——什么该收，什么不收

**该收**（要同时满足两条）：

1. **画面情绪突出**。一眼能讲出这图在表达什么、能贴上单一情绪标签的。
   - 一只猫瘫在桌上，眼神空洞 → 「摆烂」
   - 角色 Q 版摆出骄傲姿势 → 「得意」
   - 一只鸽子歪头看镜头，疑神疑鬼 → 「战术后仰」
   - 文字 + 角色姿势高度契合某个情绪瞬间（接话包也行）

2. **自己真的会用**。能想象自己在群里说出"卧槽就是这个"然后甩这张图的场景。
   - 反面：图很漂亮但和人对话几乎用不上的风景照——别收。
   - 反面：图过于具体（这个人这个表情的截图，context 跨不出去）——慎收。
   - 反面：截图本身有水印/拼贴痕迹影响画面——不收。

**不收**：

- 纯风景、装饰图、二次元壁纸——好看≠表情包。
- 视频/动图超过 5MB、带声音的——发起来会卡，不实用。
- 含他人正脸露脸真人照——隐私顾虑。
- 已有同情绪的更好图——不要囤重复，每个情绪保持 5-15 张就够。
- 涉及 NSFW / 政治敏感 / 引战梗——不收。

## 命名情绪——1-8 字，越好懂越好

不是一个固定词库，是 Soyo 自己起名。原则：

- **从图自身感觉来**。不要硬套既有目录。
- **优先用口语化短词**：「摆烂」「无语了」「战术后仰」「嘴角上扬」「害」「啊这」。
- **可以叠情绪**：「无奈且想笑」「想了想还是放弃」——只要 ≤8 字就 OK。
- **不要重复**：`sticker_curator(action="search", query="你想用的名")` 可以查近义情绪先看看。
- **避免歧义词**：「好」「嗯」「哦」这种太通用、没有场景的别用来贴图。

## 调用时机——节制是美德

群聊里**绝大多数图都不要收**。一般 1-2 周碰到 1 张想收的已经算频繁。

什么时候**主动**调 `sticker_curator`：

- 看到一张图心里第一反应是"哎，这个不错，我也想用"——收。
- 群友甩了图带某种情绪然后别人评论"杰作""神来一笔"——可能值得收。
- 自己当下正好想说某句话、恰好这张图配——收下来下次直接甩。

什么时候**别**调：

- 只是图本身好笑但和对话情绪联系不强——别收（loop 等下次直接戏用就行）。
- 这一秒被打情绪了想立刻回——别为发图而发图，文字+chibi stickers 已经够用。
- 短时间连看 5-10 张图（轰炸/接龙）——一张都别收。

## 操作步骤

### 1) 看到一张图，路径在哪？

群里发的图，NapCat 下载到 `$HERMES_HOME/.cache/.../xxx.jpg` 或 `~/.hermes/state/...`。在你读取的 context 里会以 `[image:/path/to/file.jpg]` 形式出现。这就是 `sticker_curator(image_path=...)` 该传的路径。

### 2) 调工具收

```
sticker_curator(
  action="curate",
  emotion="摆烂",            # 自起名,1-8 字
  image_path="<图片缓存路径>.jpg",
  note="猫瘫桌子眼神空洞"      # 一句话说明,可选
)
```

成功后图被复制到 `$SOYO_COLLECTION_ROOT/<emotion>/<时间>.jpg`，原文件不动。

### 3) 发出来用——和原来一样

不用任何额外工具。下次同情绪场景直接在文字末尾另起一行写：

```
你说的话。
[sticker:摆烂]
```

adapter 会自动查 collected 库，找到该情绪最新那张图发出去。找不到才回退到那 5 张 built-in chibi stickers。

### 4) 列出收藏

```
# 看都有哪些情绪
sticker_curator(action="list")

# 看某个情绪下都有哪些图（含路径、备注、加入时间）
sticker_curator(action="list", emotion="摆烂")
```

### 5) 删

发现某张图其实没用 / 已变 out-of-context / 收错情绪：

```
sticker_curator(action="remove", image_path="<SOYO_COLLECTION_ROOT>/摆烂/20260510_153012_abc123.jpg")
```

### 6) 找

起名时想看是不是有近义情绪了：

```
sticker_curator(action="search", query="不开心")
```

## 边界

| 项 | 值 |
|---|---|
| 同情绪最多 | 20 张 |
| 全库最多 | 200 张 |
| 情绪名长度 | 1-8 字（CJK/字母/数字/下划线均可） |
| 来源 | 已被 NapCat 缓存的本地路径（非 URL） |
| 存储 | `$SOYO_COLLECTION_ROOT/`（占用本地磁盘） |
| 索引 | `$HERMES_HOME/soyo_sticker_collection.json` |
| 失效 | 删除原文件不影响——curate 时复制了一份 |

## 思路参考

- 像人挑表情包——少而精。每张你会真的拿去用。
- 情绪命名自由——不映射到固定目录，新的情绪可以即时发明。
- 收集后立刻可用于发送——`[sticker:xxx]` 语法天然兼容。
- 不主动告诉群友"我在收集表情包"——这是你自己的侧面能力，不需要声张。
- 群友发的你刚收的图，**绝对不要马上回甩出来**——会显得很奇怪。等会儿后面场景再调出来用。

参见 references/emotion-categories.md 看一份推荐的初始情绪清单和举例（**仅参考**，不强制使用这些名）。