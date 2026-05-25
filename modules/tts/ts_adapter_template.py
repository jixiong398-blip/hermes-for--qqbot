"""
=== TTS 适配器 — 通用角色模板 ===
AI 代理重新配置只需改两个变量：
  1. REF_DIR — 参考音频目录路径
  2. VOICE_CONFIG — 10 个情绪的 {文件名, 参考文本} 映射表

启动: python ts_adapter.py
端口: 5000
调用: POST http://127.0.0.1:5000/v1/audio/speech {"input":"こんにちは","voice":null}
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json, os, urllib.request, urllib.parse, re, subprocess, tempfile

# ─── 配置区（换角色改这里）──────────────────────────
GPT_SOVITS_URL = "http://127.0.0.1:9880"
REF_DIR = r"E:\ai\tts_refs"  # 改成你的参考音频目录

VOICE_CONFIG = {
    # 以下为示例 — AI 代理请用实际值替换
    "default":    {"file": "default.wav",    "ref_text": "参考音频里说的话"},
    "happy":      {"file": "happy.wav",      "ref_text": "参考音频里说的话"},
    "laugh":      {"file": "laugh.wav",      "ref_text": "参考音频里说的话"},
    "sad":        {"file": "sad.wav",        "ref_text": "参考音频里说的话"},
    "angry":      {"file": "angry.wav",      "ref_text": "参考音频里说的话"},
    "surprise":   {"file": "surprise.wav",   "ref_text": "参考音频里说的话"},
    "question":   {"file": "question.wav",   "ref_text": "参考音频里说的话"},
    "apologize":  {"file": "apologize.wav",  "ref_text": "参考音频里说的话"},
    "agree":      {"file": "agree.wav",      "ref_text": "参考音频里说的话"},
    "flirty":     {"file": "flirty.wav",     "ref_text": "参考音频里说的话"},
}
# ─── 配置区结束 ────────────────────────────────────

EMOTION_PATTERNS = {
    "happy": [r"开心", r"高兴", r"太好[了啦]", r"真棒", r"好喜欢", r"嘻嘻", r"嘿嘿", r"♪",
              r"嬉しい", r"楽しい", r"よかった", r"最高", r"わあ", r"えへへ"],
    "laugh": [r"哈哈", r"笑死", r"太好笑[了啦]", r"233", r"草[了]*$",
              r"はは", r"笑っ", r"面白い", r"うける", r"あはは"],
    "sad": [r"难过", r"伤心", r"哭了", r"寂寞", r"悲伤", r"呜呜", r"唉…",
            r"悲しい", r"寂しい", r"泣[いき]", r"辛い"],
    "angry": [r"生气[了啦]?", r"火大", r"气死", r"可恶",
              r"怒っ", r"むかつく", r"許せない"],
    "surprise": [r"震惊", r"真的[嘛]?[?!]*$", r"居然", r"竟然", r"天哪", r"不会吧",
                 r"まさか", r"びっくり", r"本当[に]?\s*[!?]*$", r"すごい", r"嘘[でしょ]?"],
    "question": [r"[真的嘛]?\?\s*$", r"为什么", r"怎么[会样]?", r"是不是[呢嘛]?",
                 r"なぜ", r"どうして", r"どう[なの]?", r"かしら"],
    "apologize": [r"对不起", r"抱歉", r"不好意思", r"我错了",
                  r"ごめん", r"すみません", r"悪かった", r"申し訳"],
    "agree": [r"说得对", r"没错[呢哦]?", r"同意",
              r"そうね", r"確かに", r"もちろん", r"いいよ"],
    "flirty": [r"爱你哟", r"么么", r"抱抱", r"撒娇",
               r"大好き", r"好き[だよ]?", r"ぎゅっ", r"ちゅっ"],
}

EMOTION_WEIGHTS = {
    "laugh": 3, "angry": 3, "surprise": 2, "flirty": 2,
    "sad": 1.5, "apologize": 1.5, "question": 1.2,
    "happy": 1, "agree": 1,
}

def detect_emotion(text):
    scores = {}
    for emotion, patterns in EMOTION_PATTERNS.items():
        score = sum(1 for p in patterns if re.search(p, text))
        if score > 0:
            scores[emotion] = score * EMOTION_WEIGHTS.get(emotion, 1)
    if not scores:
        return "default"
    return max(scores, key=scores.get)

class TTSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        elif self.path == "/voices":
            voices = {k: {"file": v["file"]} for k, v in VOICE_CONFIG.items()}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(voices).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/v1/audio/speech":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = body.get("input", "")
            voice = body.get("voice") or detect_emotion(text)
            if voice not in VOICE_CONFIG:
                voice = "default"

            cfg = VOICE_CONFIG[voice]
            ref_path = os.path.join(REF_DIR, cfg["file"])
            if not os.path.exists(ref_path):
                self.send_error(500, f"ref audio not found: {ref_path}")
                return

            params = urllib.parse.urlencode({
                "text": text[:400],
                "text_lang": "auto",
                "ref_audio_path": ref_path,
                "prompt_lang": "ja",
                "prompt_text": cfg["ref_text"],
                "text_split_method": "cut5",
                "batch_size": 1,
                "media_type": "wav",
                "streaming_mode": "false",
            })
            resp = urllib.request.urlopen(f"{GPT_SOVITS_URL}/tts?{params}", timeout=120)
            wav_data = resp.read()

            # Convert WAV to MP3
            tmp_wav = os.path.join(tempfile.gettempdir(), f"tts_{os.urandom(4).hex()}.wav")
            tmp_mp3 = os.path.join(tempfile.gettempdir(), f"tts_{os.urandom(4).hex()}.mp3")
            with open(tmp_wav, "wb") as f:
                f.write(wav_data)
            subprocess.run(["ffmpeg", "-y", "-i", tmp_wav, "-b:a", "64k", tmp_mp3],
                         capture_output=True, creationflags=0x08000000 if os.name == 'nt' else 0)
            with open(tmp_mp3, "rb") as f:
                mp3_data = f.read()
            for p in (tmp_wav, tmp_mp3):
                try: os.unlink(p)
                except OSError: pass

            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", len(mp3_data))
            self.end_headers()
            self.wfile.write(mp3_data)
        except Exception as e:
            self.send_error(500, str(e))

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 5000), TTSHandler)
    print(f"TTS Adapter running on :5000 — REF_DIR={REF_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
