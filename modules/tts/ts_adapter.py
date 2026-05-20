"""
GPT-SoVITS TTS Adapter with emotion detection
Reference audio: E:\ai\tts_refs\
Features: WAV to MP3 conversion for NapCat compatibility
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.parse
import re
import subprocess
import tempfile

GPT_SOVITS_URL = "http://127.0.0.1:9880"
REF_DIR = r"E:\ai\tts_refs"

VOICE_CONFIG = {
    "default": {"file": "default_关心.wav", "ref_text": "……大丈夫？ホントだね……"},
    "happy": {"file": "happy_开心.wav", "ref_text": "こっちは、遅刻常習犯ご一行様、だって。"},
    "laugh": {"file": "laugh_大笑.wav", "ref_text": "だって、さっきのタキちゃん！"},
    "sad": {"file": "sad_悲伤.wav", "ref_text": "……はず……私は、みんなのために……"},
    "angry": {"file": "angry_愤怒.wav", "ref_text": "なぜ『春日影』やったの！サキちゃん、泣いてた……どれだけ傷つけたかわかってる？"},
    "surprise": {"file": "surprise_震惊.wav", "ref_text": "あ、宿題見せてあげる約束もしてるんだった。"},
    "question": {"file": "question_疑问.wav", "ref_text": "わかってないでしょ。本当に約束できる？"},
    "apologize": {"file": "apologize_道歉.wav", "ref_text": "うーん……そうなんだ。こっちこそごめんね。"},
    "agree": {"file": "agree_赞同.wav", "ref_text": "すごく熱かったね。私も同じこと考えてた。"},
    "flirty": {"file": "flirty_撒娇.wav", "ref_text": "……ちゃんのせいじゃないよ。あんまり自分のことを責めないで。"},
}

EMOTION_PATTERNS = {
    "happy": [r"开心", r"高兴", r"太好[了啦]", r"真棒", r"好喜欢", r"嘻嘻", r"嘿嘿", r"♪",
              r"嬉しい", r"楽しい", r"よかった", r"最高", r"わあ", r"えへへ"],
    "laugh": [r"哈哈", r"笑死", r"太好笑[了啦]", r"233", r"草[了]*$",
              r"はは", r"笑っ", r"面白い", r"うける", r"あはは"],
    "sad": [r"难过", r"伤心", r"哭了", r"寂寞", r"悲伤", r"呜呜", r"唉…",
            r"悲しい", r"寂しい", r"泣[いき]", r"辛い"],
    "angry": [r"生气[了啦]?", r"火大", r"气死", r"可恶",
              r"怒っ", r"むかつく", r"許せない"],
    "surprise": [r"震惊", r"真[的嘛]?[?!]*$", r"居然", r"竟然", r"天哪", r"不会吧",
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

# Priority weights: rare emotions get priority over common ones
# This prevents "什么" from always triggering surprise
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
    return max(scores, key=scores.get) if scores else "default"


def convert_wav_to_mp3(wav_data):
    """Convert WAV data to MP3 using ffmpeg"""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
        wav_file.write(wav_data)
        wav_path = wav_file.name
    
    mp3_path = wav_path.replace('.wav', '.mp3')
    
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', wav_path, '-b:a', '192k', mp3_path
        ], capture_output=True, check=True)
        
        with open(mp3_path, 'rb') as f:
            mp3_data = f.read()
        
        return mp3_data
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)
        if os.path.exists(mp3_path):
            os.remove(mp3_path)


class TTSHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        elif self.path == '/voices':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            voices = [{"id": k, "file": v["file"]} for k, v in VOICE_CONFIG.items()]
            self.wfile.write(json.dumps({"voices": voices}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/v1/audio/speech':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length)
                data = json.loads(body)
                
                text = data.get('input', data.get('text', ''))
                voice = data.get('voice', None)
                
                if not text:
                    self.send_error_response(400, "No text provided")
                    return
                
                if voice is None:
                    voice = detect_emotion(text)
                
                config = VOICE_CONFIG.get(voice, VOICE_CONFIG["default"])
                ref_audio = os.path.join(REF_DIR, config["file"]).replace("\\", "/")
                
                if not os.path.exists(ref_audio):
                    fallback = None
                    try:
                        for fname in os.listdir(REF_DIR):
                            if fname.lower().endswith((".wav", ".mp3", ".amr", ".flac", ".ogg", ".aac", ".m4a")):
                                fallback = os.path.join(REF_DIR, fname)
                                break
                    except FileNotFoundError:
                        fallback = None
                    if fallback:
                        ref_audio = fallback
                    else:
                        self.send_error_response(400, f"Reference audio not found: {ref_audio}")
                        return
                
                params = urllib.parse.urlencode({
                    "text": text,
                    "text_lang": "auto",
                    "ref_audio_path": ref_audio,
                    "prompt_text": config["ref_text"],
                    "prompt_lang": "ja"
                })
                url = f"{GPT_SOVITS_URL}/tts?{params}"
                
                req = urllib.request.Request(url)
                resp = urllib.request.urlopen(req, timeout=120)
                
                wav_data = resp.read()
                
                # Convert WAV to MP3
                mp3_data = convert_wav_to_mp3(wav_data)
                
                self.send_response(200)
                self.send_header('Content-Type', 'audio/mpeg')
                self.send_header('X-Emotion', voice)
                self.send_header('Content-Length', str(len(mp3_data)))
                self.end_headers()
                self.wfile.write(mp3_data)
                
            except Exception as e:
                self.send_error_response(500, str(e))
        else:
            self.send_response(404)
            self.end_headers()

    def send_error_response(self, code, message):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode())


if __name__ == '__main__':
    from socketserver import ThreadingMixIn
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
    print("GPT-SoVITS TTS Adapter (5000) - Emotion Detection + MP3 Output")
    print(f"Reference audio dir: {REF_DIR}")
    print("Pre-warming GPT-SoVITS...")
    try:
        import urllib.request, urllib.parse
        params = urllib.parse.urlencode({
            "text": "。",
            "text_lang": "auto",
            "ref_audio_path": os.path.join(REF_DIR, VOICE_CONFIG["default"]["file"]),
            "prompt_text": VOICE_CONFIG["default"]["ref_text"],
            "prompt_lang": "ja"
        })
        urllib.request.urlopen(urllib.request.Request(f"{GPT_SOVITS_URL}/tts?{params}"), timeout=120)
        print("Pre-warm complete")
    except Exception as e:
        print(f"Pre-warm skipped: {e}")
    print("Output format: MP3 (WAV converted)")
    ThreadingHTTPServer(('127.0.0.1', 5000), TTSHandler).serve_forever()
