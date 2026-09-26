#!/usr/bin/env python3
"""
YouTube Anki Vocabulary Miner & Study Pipeline
Author: Lindy (CTO Architecture)
Function:
  1. YouTube URL -> Subtitle & Metadata Extraction (via Proxy & youtube_transcript_api)
  2. CEFR C1/C2 Advanced Vocabulary & Idiomatic Phrase Mining (via local agy)
  3. Direct Anki Flashcard Injection via AnkiConnect (Parent: YouTube -> Subdeck: YouTube::<Title>)
  4. Obsidian Study Note Generation & Silent VMark Workspace Mounting
"""

import os
import sys
import re
import json
import time
import argparse
import urllib.request
import urllib.parse
import subprocess
import asyncio
import base64
import hashlib
import tempfile
import shutil
import edge_tts
from datetime import datetime, timezone, timedelta
from config_loader import get_config

CONFIG = get_config()

# Proxy settings for YouTube access
PROXY_URL = CONFIG.get("telegram", {}).get("proxy_url") or os.environ.get("HTTP_PROXY") or ""
if PROXY_URL:
    os.environ["HTTP_PROXY"] = PROXY_URL
    os.environ["HTTPS_PROXY"] = PROXY_URL
    os.environ["http_proxy"] = PROXY_URL
    os.environ["https_proxy"] = PROXY_URL
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

BEIJING_TZ = timezone(timedelta(hours=8))
OBSIDIAN_VAULT = os.path.expanduser(CONFIG.get("paths", {}).get("obsidian_vault", "")) if CONFIG.get("paths", {}).get("obsidian_vault") else ""
OUTPUT_DIR = os.path.join(OBSIDIAN_VAULT, "English", "YouTube content") if OBSIDIAN_VAULT else os.path.expanduser("~/anki-video-miner-notes")
RAW_TRANSCRIPT_DIR = os.path.join(OUTPUT_DIR, "Raw Transcripts")
ANKI_CONNECT_URL = CONFIG.get("anki", {}).get("connect_url") or "http://127.0.0.1:8765"
ANKI_VOICE = CONFIG.get("anki", {}).get("voice") or os.environ.get("ANKI_VOICE") or "en-US-ChristopherNeural"
ANKI_DECK_PREFIX = CONFIG.get("anki", {}).get("deck_prefix") or "YouTube"
ANKI_MODEL_NAME = CONFIG.get("anki", {}).get("model_name") or "Cloze"
AUTO_SYNC = CONFIG.get("anki", {}).get("auto_sync", True)
STAGING_FILE = os.path.expanduser(CONFIG.get("paths", {}).get("staging_file") or "~/.config/anki_video_staging.json")
KNOWN_WORDS_FILE = os.path.expanduser(CONFIG.get("paths", {}).get("known_words_file") or "~/.config/english_known_words.txt")


def load_known_words():
    if os.path.exists(KNOWN_WORDS_FILE):
        try:
            with open(KNOWN_WORDS_FILE, "r", encoding="utf-8") as f:
                return set(line.strip().lower() for line in f if line.strip())
        except Exception:
            pass
    return set()


def add_known_words(words):
    try:
        os.makedirs(os.path.dirname(KNOWN_WORDS_FILE), exist_ok=True)
        existing = load_known_words()
        to_add = [w.strip().lower() for w in words if w.strip().lower() and w.strip().lower() not in existing]
        if to_add:
            with open(KNOWN_WORDS_FILE, "a", encoding="utf-8") as f:
                for w in to_add:
                    f.write(f"{w}\n")
    except Exception:
        pass


def load_all_staging_sessions():
    if not os.path.exists(STAGING_FILE):
        return {"latest_video_id": None, "sessions": {}}
    try:
        with open(STAGING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "sessions" in data and isinstance(data["sessions"], dict):
            now = time.time()
            valid_sessions = {k: v for k, v in data["sessions"].items() if now - v.get("timestamp", 0) < 172800}
            data["sessions"] = valid_sessions
            return data
        elif "candidates" in data:
            vid = data.get("meta", {}).get("video_id") or "legacy_session"
            return {
                "latest_video_id": vid,
                "sessions": {vid: data}
            }
        return {"latest_video_id": None, "sessions": {}}
    except Exception:
        return {"latest_video_id": None, "sessions": {}}


def save_all_staging_sessions(data):
    try:
        os.makedirs(os.path.dirname(STAGING_FILE), exist_ok=True)
        with open(STAGING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def extract_video_id(url_or_id):
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url_or_id):
        return url_or_id
    patterns = [
        r'(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/|\/live\/|\/e\/)([a-zA-Z0-9_-]{11})',
        r'[\?\&]v=([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    return None


def get_video_title_and_author(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("title", f"YouTube Video {video_id}").strip(), data.get("author_name", "YouTube Creator").strip()
    except Exception:
        pass

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            m = re.search(r"<title>(.*?)</title>", html)
            if m:
                clean_title = m.group(1).replace(" - YouTube", "").strip()
                if clean_title:
                    return clean_title, "YouTube Creator"
    except Exception:
        pass

    return f"YouTube Video {video_id}", "YouTube Creator"


def try_rotate_clash_node():
    """检测到 IP 封禁时，自动通过 Clash Verge 控制接口无缝轮换至优质海外节点"""
    sock = "/tmp/verge/verge-mihomo.sock"
    if not os.path.exists(sock):
        return False
    try:
        cmd = ["curl", "-s", "--unix-socket", sock, "http://localhost/proxies/🔰%20选择节点"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if res.returncode != 0 or not res.stdout:
            return False
        d = json.loads(res.stdout)
        now_node = d.get("now", "")
        all_nodes = d.get("all", [])
        preferred = ["日本", "香港", "新加坡", "美国", "IEPL", "JP", "HK", "US", "SG"]
        candidates = [n for n in all_nodes if n != now_node and any(k in n for k in preferred)]
        if not candidates:
            candidates = [n for n in all_nodes if n != now_node]
        if candidates:
            next_node = candidates[0]
            put_cmd = [
                "curl", "-s", "-X", "PUT", "--unix-socket", sock,
                "http://localhost/proxies/🔰%20选择节点",
                "-d", json.dumps({"name": next_node})
            ]
            subprocess.run(put_cmd, capture_output=True, text=True, timeout=3)
            time.sleep(1.2)
            return True
    except Exception:
        pass
    return False


def fetch_transcript(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        RequestBlocked,
        IpBlocked,
        NoTranscriptFound,
        TranscriptsDisabled,
    )

    max_retries = 2
    transcript_list = None
    for attempt in range(max_retries + 1):
        ytt = YouTubeTranscriptApi()
        try:
            transcript_list = ytt.list(video_id)
            break
        except (RequestBlocked, IpBlocked) as e:
            if attempt < max_retries and try_rotate_clash_node():
                time.sleep(1.0)
                continue
            raise RuntimeError(f"YOUTUBE_IP_BLOCKED: YouTube 拦截了当前代理节点 IP（Anti-bot 封禁）: {e}")
        except (NoTranscriptFound, TranscriptsDisabled) as e:
            raise RuntimeError(f"NO_TRANSCRIPTS: 该视频未提供英文字幕（作者未上传且 YouTube 未生成自动字幕）: {e}")
        except Exception as e:
            err_str = str(e).lower()
            if any(k in err_str for k in ["blocked", "ipblocked", "requestblocked", "429"]):
                if attempt < max_retries and try_rotate_clash_node():
                    time.sleep(1.0)
                    continue
                raise RuntimeError(f"YOUTUBE_IP_BLOCKED: YouTube 拦截了当前代理节点 IP: {e}")
            if attempt == max_retries:
                raise RuntimeError(f"无法检索视频字幕: {e}")

    if transcript_list is None:
        raise RuntimeError(f"NO_TRANSCRIPTS: 视频 {video_id} 未找到任何可用字幕。")

    # 1. 优先获取人工英文字幕
    for code in ["en", "en-US", "en-GB", "en-CA", "en-AU"]:
        try:
            t = transcript_list.find_manually_created_transcript([code])
            return t.fetch()
        except Exception:
            pass

    # 2. 其次获取自动生成英文字幕
    for code in ["en", "en-US", "en-GB", "en-CA", "en-AU"]:
        try:
            t = transcript_list.find_generated_transcript([code])
            return t.fetch()
        except Exception:
            pass

    # 3. 匹配任何语言代码以 en 开头的字幕 (如 en-orig, en-x-autogen 等)
    for t in transcript_list:
        if getattr(t, "language_code", "").startswith("en"):
            try:
                return t.fetch()
            except Exception:
                pass

    # 4. 如果有其他语言字幕，尝试翻译为英文
    for t in transcript_list:
        if getattr(t, "is_translatable", False):
            try:
                return t.translate("en").fetch()
            except Exception:
                pass

    # 5. 最后保底：获取任意第一个可用字幕
    for t in transcript_list:
        try:
            return t.fetch()
        except Exception:
            pass

    raise RuntimeError(f"NO_TRANSCRIPTS: 视频 {video_id} 未找到任何可用字幕。")


def format_timestamp(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"[{h:02d}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def process_raw_transcript(transcript_data):
    lines = []
    current_chunk = []
    last_time = 0.0

    for item in transcript_data:
        if isinstance(item, dict):
            text = item.get("text") or ""
            start = item.get("start") or 0.0
        else:
            text = getattr(item, "text", "") or ""
            start = getattr(item, "start", 0.0) or 0.0

        try:
            start_float = float(start)
        except (ValueError, TypeError):
            start_float = 0.0

        clean_text = str(text).replace("\n", " ").strip()
        if not clean_text or clean_text.lower() in ("[music]", "[applause]", "[laughter]", "♪♪♪", "[♪♪♪]", "♪"):
            continue

        if not current_chunk:
            last_time = start_float
            current_chunk.append(clean_text)
        elif start_float - last_time < 20 and len(" ".join(current_chunk)) < 220:
            current_chunk.append(clean_text)
        else:
            lines.append(f"{format_timestamp(last_time)} {' '.join(current_chunk)}")
            current_chunk = [clean_text]
            last_time = start_float

    if current_chunk:
        lines.append(f"{format_timestamp(last_time)} {' '.join(current_chunk)}")

    full_formatted = "\n\n".join(lines)
    full_plain = " ".join([l.split(" ", 1)[-1] if " " in l else l for l in lines])
    return full_formatted, full_plain


def sanitize_filename(name):
    clean = re.sub(r'[\/\\:\*\?\"<>\|]', '_', name).strip()
    return re.sub(r'\s+', ' ', clean)


def sanitize_deck_name(title):
    # Anki uses :: for subdecks; strip other delimiters
    clean = re.sub(r'[:/\\*?"<>|]', ' ', title)
    clean = re.sub(r'\s+', ' ', clean).strip()
    if len(clean) > 55:
        clean = clean[:52].rstrip() + "..."
    return clean or "Untitled Video"


def parse_c1_c2_json_robust(output):
    """
    鲁棒解析 LLM 返回的 JSON 列表，支持：
    1. 标准 JSON Array 直接加载
    2. 字段间缺失逗号自动修复 (如 "foo": "bar"\\n "baz": "qux")
    3. 对象间缺失逗号自动修复 (如 }\\n {)
    4. 尾部多余逗号清洗
    5. 单对象扫描兜底提取，确保最大限度挽救有效提炼卡片
    """
    if not output:
        raise ValueError("输入为空，无法解析 JSON")

    # 提取最外层的列表候选区域
    candidate_str = output
    m_array = re.search(r'\[\s*\{.*\}\s*\]', output, re.DOTALL)
    if m_array:
        candidate_str = m_array.group(0)
    else:
        candidate_str = re.sub(r'^```(?:json)?\s*', '', candidate_str, flags=re.MULTILINE)
        candidate_str = re.sub(r'^```\s*$', '', candidate_str, flags=re.MULTILINE).strip()

    # 1. 尝试直接加载
    try:
        data = json.loads(candidate_str)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # 2. 文本级语法修补
    repaired = candidate_str
    # 修复键值对之间遗漏的逗号
    repaired = re.sub(r'(\"[^\"]*\")\s*\n\s*(\"[a-zA-Z0-9_]+\"\s*:)', r'\1,\n\2', repaired)
    # 修复对象 } 与 { 之间遗漏的逗号
    repaired = re.sub(r'\}\s*\n\s*\{', r'},\n{', repaired)
    # 移除对象或数组末尾的多余逗号
    repaired = re.sub(r',\s*([\]\}])', r'\1', repaired)

    try:
        data = json.loads(repaired)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # 3. 逐个对象扫描兜底提取
    items = []
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(repaired):
        pos = repaired.find('{', pos)
        if pos == -1:
            break
        try:
            obj, end = decoder.raw_decode(repaired[pos:])
            if isinstance(obj, dict) and obj.get("word"):
                items.append(obj)
            pos += max(end, 1)
        except Exception:
            end_brace = repaired.find('}', pos)
            if end_brace != -1:
                chunk = repaired[pos:end_brace + 1]
                chunk_fixed = re.sub(r'(\"[^\"]*\")\s*\n\s*(\"[a-zA-Z0-9_]+\"\s*:)', r'\1,\n\2', chunk)
                chunk_fixed = re.sub(r',\s*\}', '}', chunk_fixed)
                try:
                    obj = json.loads(chunk_fixed)
                    if isinstance(obj, dict) and obj.get("word"):
                        items.append(obj)
                except Exception:
                    pass
                pos = end_brace + 1
            else:
                pos += 1

    if items:
        return items

    raise ValueError(f"无法解析 JSON 词汇数组: {output[:300]}")


def mine_c1_c2_vocabulary(title, full_text, user_query=None, exclude_words=None):
    """
    Call local agy engine to extract CEFR C1/C2 words, idioms, and phrases from transcript.
    """
    # Restrict text length to avoid token bloat (approx 18,000 characters is plenty for ~35 min video)
    truncated_text = full_text[:80000] if len(full_text) > 80000 else full_text

    instruction_parts = []
    if user_query and user_query.strip():
        instruction_parts.append(f"用户专项指令：{user_query.strip()}")
    if exclude_words:
        clean_excludes = [w.strip() for w in exclude_words if w.strip()]
        if clean_excludes:
            ex_str = ", ".join(clean_excludes[:60])
            instruction_parts.append(f"已提炼词汇列表（严禁重复，必须挖掘未收录的新高阶表达）：{ex_str}")

    instruction = ("\n".join(instruction_parts) + "\n") if instruction_parts else ""

    prompt = f"""You are a Master English Lexicographer and Cognitive Reduction Specialist for advanced English learners (CEFR C1+).
Analyze the following YouTube video transcript and extract the most valuable, sophisticated expressions that cause comprehension or production friction.

Video Title: "{title}"
{instruction}
TRANSCRIPT CONTENT:
{truncated_text}

STRICT SELECTION CRITERIA:
1. Target Scope: Focus strictly on CEFR C1/C2 level multi-word expressions, idioms, high-register phrasal verbs, polysemy (熟词僻义), domain collocations, and argumentative syntactic frames.
2. DIFFICULTY CALIBRATION (CRITICAL):
   - Strictly reject all basic, everyday B1/B2 words.
   - ALSO FILTER OUT common, familiar C1 words that near-native learners already know passively (e.g. ubiquitous, perspective, diverse, crucial, fundamental, comprehensive, navigate, enhance).
   - PRIORITIZE LESS COMMON, NUANCED, OR DECEPTIVE EXPRESSIONS across these categories:
     a) 熟词僻义 / Polysemy: Common base words used in rare, high-register, or non-intuitive senses (e.g. champion as verb, table a motion, harbor doubts, weather the storm, plastic mind).
     b) 欺骗性习语 / Deceptive Idioms: Expressions where literal meaning misleads (e.g. play devil's advocate, cut no ice, fall between two stools, take with a grain of salt).
     c) 高阶复述短语 / Phrasal Collocations: Natural native verb/noun combinations with high expressive leverage (e.g. double down on, boil down to, marshal evidence, strike a chord, fall short of).
     d) 论证句型 / Argumentative Frames: Complex rhetorical structures that structure reasoning (e.g. It is one thing to..., but quite another to...).
     e) 核心概念 / Key Concepts: Author-specific models or dense academic constructs.
3. ZERO ARTIFICIAL QUANTITY RESTRICTIONS (CONTENT-DRIVEN):
   - Do NOT cap or arbitrarily limit the number of items. The content difficulty strictly dictates the extraction volume:
     * 难的多挖点：If the video is intellectually dense, long, or rich in sophisticated language, exhaustively extract all genuinely challenging C1/C2 tokens, collocations, and phrases (whether that is 15, 25, or 35+ items).
     * 简单的少挖点：If the video is simple, casual, or conversational, extract ONLY the few non-trivial items that truly qualify, even if just 2 or 3 items.
   - Sole criterion: Whether an advanced learner might genuinely not understand, misinterpret, or struggle to spontaneously produce the expression. Zero artificial caps, zero padding.
4. Grounding: Each item MUST have a verbatim context sentence from the transcript.

OUTPUT FORMAT:
Output ONLY a strictly valid JSON array of objects. No markdown preambles, no explanation outside JSON.
Each object must have the following fields:
- "word": string (the target word, phrase, idiom, or syntactic frame)
- "ipa": string (accurate GA phonetic IPA; empty string if syntactic frame)
- "pos": string (part of speech: "v.", "adj.", "n.", "idiom", "phr. v.", "frame", "collocation")
- "level": string ("C1" or "C2")
- "category": string (must be one of: ["熟词僻义", "论证句型", "欺骗性习语", "核心概念", "复述搭配", "高阶短语"])
- "definition_zh": string (concise, precise Chinese meaning tailored to this specific video context)
- "short_hint": string (sharp 2-6 character Chinese retrieval cue)
- "explanation": string (1 concise sentence explaining the nuance, usage context, or why it fits C1/C2)
- "context_sentence": string (the exact original sentence where it appeared)
- "cloze_sentence": string (the verbatim context_sentence where the exact target token in its actual inflected form is wrapped as {{c1::exact_token::short_hint}})
- "sentence_translation": string (natural, idiomatic Chinese translation of the context sentence)

Example output item:
{{
  "word": "champion",
  "ipa": "/ˈtʃæm.pi.ən/",
  "pos": "v.",
  "level": "C1",
  "category": "熟词僻义",
  "definition_zh": "公开捍卫，积极拥护",
  "short_hint": "积极捍卫",
  "explanation": "此处不作名词冠军，而是作动词表示支持和捍卫某种主张或政策。",
  "context_sentence": "She championed the new policy despite fierce opposition.",
  "cloze_sentence": "She {{c1::championed::积极捍卫}} the new policy despite fierce opposition.",
  "sentence_translation": "尽管遭到强烈反对，她依然坚定地捍卫并推进这项新政策。"
}}
"""

    from llm_client import call_llm

    try:
        output = call_llm(prompt, json_mode=True)
    except Exception as exc:
        raise RuntimeError(f"AI 词汇提炼失败: {exc}")

    raw_vocab = parse_c1_c2_json_robust(output)
    if exclude_words:
        ex_set = set(w.strip().lower() for w in exclude_words if w.strip())
        raw_vocab = [v for v in raw_vocab if v.get("word", "").strip().lower() not in ex_set]
    return raw_vocab


def anki_request(payload, timeout=6.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ANKI_CONNECT_URL,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        if res.get("error"):
            raise RuntimeError(f"AnkiConnect 错误: {res.get('error')}")
        return res


def ensure_anki_running():
    try:
        anki_request({"action": "version", "version": 6}, timeout=1.5)
        return True
    except Exception:
        pass

    # Try launching Anki in background
    try:
        subprocess.Popen(["open", "-a", "Anki", "--background"])
        for _ in range(8):
            time.sleep(1.0)
            try:
                anki_request({"action": "version", "version": 6}, timeout=1.5)
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def build_cloze_fallback(sentence, word, hint):
    # Normalize any single-brace {c1::...} or existing cloze into standard {{c1::...}}
    m_cloze = re.search(r'\{+c1::(.*?)(?:::([^}]+))?\}+', sentence)
    if m_cloze:
        target = m_cloze.group(1).strip()
        cue = (m_cloze.group(2) or hint).strip()
        return re.sub(r'\{+c1::.*?\}+', lambda _: f"{{{{c1::{target}::{cue}}}}}", sentence, count=1)

    clean_word = word.strip()
    # Enforce word boundaries \b to prevent sub-string false matching, and use lambda to prevent regex escape errors
    pattern = re.compile(rf"\b{re.escape(clean_word)}\b", re.IGNORECASE)
    if pattern.search(sentence):
        return pattern.sub(lambda m: f"{{{{c1::{m.group(0)}::{hint}}}}}", sentence, count=1)

    alt_word = clean_word.replace("-", " ")
    pattern_alt = re.compile(rf"\b{re.escape(alt_word)}\b", re.IGNORECASE)
    if pattern_alt.search(sentence):
        return pattern_alt.sub(lambda m: f"{{{{c1::{m.group(0)}::{hint}}}}}", sentence, count=1)

    return f"{sentence} ({{{{c1::{clean_word}::{hint}}}}})"


async def synthesize_audio_bytes(text: str, voice: str = ANKI_VOICE) -> bytes:
    """使用 edge_tts 异步合成高保真神经语音并返回音频字节"""
    comm = edge_tts.Communicate(text, voice)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_name = tmp.name
    try:
        await comm.save(tmp_name)
        with open(tmp_name, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except Exception:
                pass


def store_audio_in_anki(filename: str, audio_bytes: bytes) -> bool:
    """通过 AnkiConnect storeMediaFile 将音频注入 Anki 媒体库"""
    try:
        b64_data = base64.b64encode(audio_bytes).decode("utf-8")
        res = anki_request({
            "action": "storeMediaFile",
            "version": 6,
            "params": {
                "filename": filename,
                "data": b64_data
            }
        })
        return res.get("error") is None
    except Exception:
        return False


def get_anki_media_dir() -> str:
    """Dynamically resolve Anki media directory via config, AnkiConnect API, or profile discovery."""
    configured = CONFIG.get("anki", {}).get("media_dir")
    if configured:
        p = os.path.expanduser(configured)
        if os.path.isdir(p):
            return p
    try:
        res = anki_request({"action": "getMediaDirPath", "version": 6})
        if res and not res.get("error") and res.get("result"):
            p = res["result"]
            if os.path.isdir(p):
                return p
    except Exception:
        pass
    candidates = [
        os.path.expanduser("~/Library/Application Support/Anki2"),
        os.path.expanduser("~/.local/share/Anki2"),
        os.path.expanduser("~/AppData/Roaming/Anki2"),
    ]
    for base in candidates:
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                if os.path.basename(root) == "collection.media":
                    return root
    return ""


async def prepare_vocab_audio(vocab_items: list, voice: str = ANKI_VOICE):
    """批量并发为词汇和例句合成高质量语音，并自动同步至 Anki 媒体库"""
    media_dir = get_anki_media_dir()

    async def process_one(item):
        word = item.get("word", "").strip()
        context = item.get("context_sentence", "").strip()

        word_hash = hashlib.md5(word.lower().encode("utf-8")).hexdigest()[:8]
        sent_hash = hashlib.md5(context.encode("utf-8")).hexdigest()[:8]

        word_fn = f"yt_w_{word_hash}.mp3"
        sent_fn = f"yt_s_{sent_hash}.mp3"

        # 1. 生词发音
        if word:
            word_path = os.path.join(media_dir, word_fn) if media_dir else None
            if word_path and os.path.exists(word_path):
                item["word_audio"] = word_fn
            else:
                try:
                    w_bytes = await synthesize_audio_bytes(word, voice)
                    success = await asyncio.to_thread(store_audio_in_anki, word_fn, w_bytes)
                    if success or (word_path and os.path.exists(word_path)):
                        item["word_audio"] = word_fn
                except Exception:
                    pass

        # 2. 原句朗读
        if context:
            sent_path = os.path.join(media_dir, sent_fn) if media_dir else None
            if sent_path and os.path.exists(sent_path):
                item["sent_audio"] = sent_fn
            else:
                try:
                    s_bytes = await synthesize_audio_bytes(context, voice)
                    success = await asyncio.to_thread(store_audio_in_anki, sent_fn, s_bytes)
                    if success or (sent_path and os.path.exists(sent_path)):
                        item["sent_audio"] = sent_fn
                except Exception:
                    pass

    tasks = [process_one(item) for item in vocab_items]
    await asyncio.gather(*tasks, return_exceptions=True)


def inject_into_anki(deck_name, vocab_items):
    if not ensure_anki_running():
        return {
            "success": False,
            "error": "Anki 未启动且无法通过 AnkiConnect (http://127.0.0.1:8765) 连通",
            "added": 0,
            "skipped": 0,
        }

    # 1. 自动创建父子牌组 YouTube::<Title>
    anki_request({"action": "createDeck", "version": 6, "params": {"deck": deck_name}})

    # 2. 批量并发合成语音并注入 Anki 媒体库
    try:
        asyncio.run(prepare_vocab_audio(vocab_items))
    except Exception as e:
        print(f"[WARN] 音频合成降级: {e}", file=sys.stderr)

    # 3. 组装完形填空 (Cloze) 卡片列表
    notes = []
    for item in vocab_items:
        word = item.get("word", "").strip()
        ipa = item.get("ipa", "").strip()
        pos = item.get("pos", "").strip()
        level = item.get("level", "C1").strip()
        def_zh = item.get("definition_zh", "").strip()
        short_hint = item.get("short_hint", "").strip() or def_zh[:6]
        explanation = item.get("explanation", "").strip()
        context = item.get("context_sentence", "").strip()
        cloze_sent = item.get("cloze_sentence", "").strip()
        trans = item.get("sentence_translation", "").strip()
        sent_audio = item.get("sent_audio", "")
        word_audio = item.get("word_audio", "")

        # 确保包含有效的 Cloze 语法 {{c1::...::...}} 并处理词边界与单双括号
        cloze_sent = build_cloze_fallback(cloze_sent if cloze_sent else context, word, short_hint)

        # 保持 Text 字段纯净自然，支持深色/黑夜模式自适应
        text_html = (
            f"<div style='font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 19px; line-height: 1.6; color: var(--text-body, #1e293b); text-align: left; padding: 4px 0;'>"
            f"{cloze_sent}"
            f"</div>"
        )

        # 单词发音按钮内联嵌入头部；例句发音嵌入翻译行
        word_audio_tag = f"<span style='margin-left: 8px;'>[sound:{word_audio}]</span>" if word_audio else ""
        sent_audio_tag = f"<span style='margin-left: 12px; flex-shrink: 0;'>[sound:{sent_audio}]</span>" if sent_audio else ""

        trans_section = ""
        if trans or sent_audio_tag:
            trans_section = (
                f"<div style='display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: var(--surface-subtle, #f8fafc); border-radius: 8px; margin-top: 14px;'>"
                f"<span style='font-size: 14px; line-height: 1.5; color: var(--text-secondary, #475569);'>{trans}</span>"
                f"{sent_audio_tag}"
                f"</div>"
            )

        back_extra_html = (
            f"<div style='font-family: -apple-system, BlinkMacSystemFont, sans-serif; text-align: left; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border-divider, #e2e8f0);'>"
            f"<div style='display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px;'>"
            f"<span style='font-size: 26px; font-weight: 700; color: var(--text-primary, #0f172a); letter-spacing: -0.02em;'>{word}</span>"
            f"<span style='font-size: 15px; color: var(--text-muted, #64748b);'>{ipa}</span>"
            f"<span style='font-size: 11px; font-weight: 600; color: var(--accent-badge-text, #4338ca); background: var(--accent-badge-bg, #eef2ff); padding: 2px 6px; border-radius: 4px;'>{level}</span>"
            f"<span style='font-size: 13px; color: var(--text-muted, #64748b); font-style: italic;'>{pos}</span>"
            f"{word_audio_tag}"
            f"</div>"
            f"<div style='font-size: 17px; font-weight: 600; color: var(--text-primary, #0f172a); margin-bottom: 8px; line-height: 1.4;'>{def_zh}</div>"
            f"<div style='font-size: 14px; line-height: 1.6; color: var(--text-muted, #64748b); margin-bottom: 4px;'>{explanation}</div>"
            f"{trans_section}"
            f"</div>"
        )

        notes.append({
            "deckName": deck_name,
            "modelName": "Cloze",
            "fields": {
                "Text": text_html,
                "Back Extra": back_extra_html,
            },
            "options": {
                "allowDuplicate": False,
                "duplicateScope": "deck"
            },
            "tags": ["YouTube", f"CEFR_{level}", "Cloze", "AutoMined"]
        })

    # 3. 逐张写入卡片，具备防重复与容错能力
    added = 0
    skipped = 0
    for note in notes:
        try:
            res = anki_request({"action": "addNote", "version": 6, "params": {"note": note}})
            if res.get("result"):
                added += 1
            else:
                skipped += 1
        except Exception as e:
            if "duplicate" in str(e).lower():
                skipped += 1
            else:
                pass

    # 4. 自动触发 AnkiWeb 云端同步，确保手机端打开即可即时复习，无需手动在 Mac 上点同步
    synced = False
    if added > 0:
        try:
            sync_res = anki_request({"action": "sync", "version": 6}, timeout=25.0)
            if sync_res.get("error") is None:
                synced = True
        except Exception as e:
            print(f"[WARN] AnkiWeb 自动同步失败 (不影响本地入库): {e}", file=sys.stderr)

    return {
        "success": True,
        "added": added,
        "skipped": skipped,
        "total": len(notes),
        "synced": synced,
    }


def is_vmark_running():
    try:
        res = subprocess.run(
            ["osascript", "-e", 'application "VMark" is running'],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False


def mount_to_vmark(file_path):
    if not is_vmark_running():
        return False
    custom_script = CONFIG.get("paths", {}).get("vmark_script")
    candidates = []
    if custom_script:
        candidates.append(os.path.expanduser(custom_script))
    candidates.append(os.path.expanduser("~/.gemini/config/skills/vmark-chat-viewer/scripts"))

    viewer_dir = None
    for c in candidates:
        if os.path.isfile(c) and c.endswith(".py"):
            viewer_dir = os.path.dirname(c)
            break
        elif os.path.isdir(c) and os.path.isfile(os.path.join(c, "open_vmark_chat.py")):
            viewer_dir = c
            break

    if not viewer_dir:
        return False

    if viewer_dir not in sys.path:
        sys.path.insert(0, viewer_dir)
    try:
        from open_vmark_chat import VMarkMCPClient
        client = VMarkMCPClient(timeout=3.0)
        client.start()
        try:
            client.call_tool("workspace", {
                "action": "open",
                "filePath": os.path.realpath(file_path)
            })
            return True
        finally:
            client.close()
    except Exception:
        return False


def save_obsidian_study_note(meta, vocab_items, deck_name, anki_res):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_title = sanitize_filename(meta["title"])
    target_file = os.path.join(OUTPUT_DIR, f"{safe_title}.md")

    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

    c1_count = sum(1 for v in vocab_items if v.get("level") == "C1")
    c2_count = sum(1 for v in vocab_items if v.get("level") == "C2")

    md = [
        "---",
        f"title: \"{meta['title']}\"",
        f"author: \"{meta['author']}\"",
        f"url: \"{meta['url']}\"",
        f"created: \"{now_str}\"",
        "tags: [english, youtube, vocabulary, c1_c2, anki]",
        "---",
        "",
        f"# {meta['title']}",
        "",
        "> [!NOTE] 视频元数据与词汇统计",
        f"> - **频道主创**：{meta['author']}",
        f"> - **视频地址**：[{meta['url']}]({meta['url']})",
        f"> - **Anki 牌组**：`{deck_name}`",
        f"> - **词汇统计**：共提炼 **{len(vocab_items)}** 个高级表达（**C1**: {c1_count} 个 / **C2**: {c2_count} 个）",
        f"> - **Anki 同步**：{'成功注入 ' + str(anki_res.get('added', 0)) + ' 张新卡片' if anki_res.get('success') else '同步失败: ' + str(anki_res.get('error'))}",
        "",
        "## 1. C1 / C2 进阶词汇与地道表达速览表",
        "",
        "| 单词 / 短语 | 音标 | 词性 | 级别 | 中文释义 | 原视频真实例句 |",
        "| :--- | :--- | :---: | :---: | :--- | :--- |",
    ]

    for item in vocab_items:
        word = item.get("word", "")
        ipa = item.get("ipa", "")
        pos = item.get("pos", "")
        level = item.get("level", "C1")
        def_zh = item.get("definition_zh", "").replace("|", "\\|")
        ctx = item.get("context_sentence", "").replace("|", "\\|")
        md.append(f"| **{word}** | `{ipa}` | {pos} | `{level}` | {def_zh} | {ctx} |")

    md.extend([
        "",
        "## 2. 语境深度解析与地道例句卡片",
        "",
    ])

    for i, item in enumerate(vocab_items, 1):
        word = item.get("word", "")
        ipa = item.get("ipa", "")
        pos = item.get("pos", "")
        level = item.get("level", "C1")
        def_zh = item.get("definition_zh", "")
        exp = item.get("explanation", "")
        ctx = item.get("context_sentence", "")
        trans = item.get("sentence_translation", "")

        md.extend([
            f"### {i}. {word} `{ipa}` ({level} · {pos})",
            f"> **中文释义**：{def_zh}",
            f">",
            f"> **深度辨析**：{exp}",
            "",
            "- **视频原句**：",
            f"  > {ctx}",
            "- **中文翻译**：",
            f"  > {trans}",
            "",
        ])

    md.extend([
        "## 3. 复习指引与刷卡提示",
        "> [!TIP] Anki 记忆闭环",
        f"> 1. 卡片已直接沉淀至 Anki 牌组 `{deck_name}` 中。",
        "> 2. 建议复习时：先看正面原句进行「完形填空」与主动回忆，再看背面核对精准释义与语境辨析。",
        "> 3. 手机端打开 AnkiMobile 点击同步即可即时复习。",
        "",
    ])

    with open(target_file, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    return target_file


def run_pipeline(url_or_id, user_query=None):
    video_id = extract_video_id(url_or_id)
    if not video_id:
        return {"status": "error", "message": f"无效的 YouTube URL 或视频 ID: {url_or_id}"}

    # 1. 抓取元数据
    title, author = get_video_title_and_author(video_id)
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    meta = {
        "video_id": video_id,
        "title": title,
        "author": author,
        "url": video_url,
    }

    # 2. 抓取字幕
    try:
        raw_items = fetch_transcript(video_id)
    except Exception as e:
        err_msg = str(e)
        if "YOUTUBE_IP_BLOCKED" in err_msg:
            return {
                "status": "network_blocked",
                "video_id": video_id,
                "title": title,
                "author": author,
                "url": video_url,
                "message": err_msg.replace("YOUTUBE_IP_BLOCKED:", "").strip()
            }
        elif "NO_TRANSCRIPTS" in err_msg:
            return {
                "status": "no_subtitles",
                "video_id": video_id,
                "title": title,
                "author": author,
                "url": video_url,
                "message": "该视频未提供英文字幕（作者未上传且 YouTube 未生成自动字幕）。"
            }
        else:
            return {
                "status": "error",
                "video_id": video_id,
                "title": title,
                "author": author,
                "url": video_url,
                "message": f"字幕拉取失败: {err_msg}"
            }

    formatted_transcript, plain_transcript = process_raw_transcript(raw_items)

    # 保存原始字幕
    os.makedirs(RAW_TRANSCRIPT_DIR, exist_ok=True)
    raw_file = os.path.join(RAW_TRANSCRIPT_DIR, f"{sanitize_filename(title)} (Raw Transcript).txt")
    try:
        with open(raw_file, "w", encoding="utf-8") as f:
            f.write(formatted_transcript)
    except Exception:
        pass

    # 3. AI 提炼 C1/C2 词汇与短语
    try:
        raw_vocab = mine_c1_c2_vocabulary(title, plain_transcript, user_query)
        if not raw_vocab or not isinstance(raw_vocab, list):
            raise ValueError("提炼结果非有效列表。")
    except Exception as e:
        return {"status": "error", "message": f"AI 提炼词汇失败: {e}"}

    # 4. 自动创建 Anki 牌组并写入卡片
    subdeck_title = sanitize_deck_name(title)
    deck_name = f"YouTube::{subdeck_title}"
    anki_res = inject_into_anki(deck_name, raw_vocab)

    # 5. 落盘 Obsidian 学习笔记 & 静默挂载 VMark
    note_file = save_obsidian_study_note(meta, raw_vocab, deck_name, anki_res)
    vmark_mounted = mount_to_vmark(note_file)

    return {
        "status": "success",
        "video_id": video_id,
        "title": title,
        "author": author,
        "url": video_url,
        "deck_name": deck_name,
        "total_extracted": len(raw_vocab),
        "anki_added": anki_res.get("added", 0),
        "anki_skipped": anki_res.get("skipped", 0),
        "anki_success": anki_res.get("success", False),
        "anki_error": anki_res.get("error"),
        "note_file": note_file,
        "vmark_mounted": vmark_mounted,
        "top_vocab": raw_vocab[:6],
    }


def run_preview(url_or_id, user_query=None):
    video_id = extract_video_id(url_or_id)
    if not video_id:
        return {"status": "error", "message": f"无效的 YouTube URL 或视频 ID: {url_or_id}"}

    title, author = get_video_title_and_author(video_id)
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    meta = {
        "video_id": video_id,
        "title": title,
        "author": author,
        "url": video_url,
    }

    # 1. 优先从本地已缓存的原始字幕中加载（极大加速追增挖掘并避免重复网络风控）
    os.makedirs(RAW_TRANSCRIPT_DIR, exist_ok=True)
    raw_file = os.path.join(RAW_TRANSCRIPT_DIR, f"{sanitize_filename(title)} (Raw Transcript).txt")
    plain_transcript = None
    formatted_transcript = None

    if os.path.exists(raw_file):
        try:
            with open(raw_file, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                formatted_transcript = content
                plain_transcript = re.sub(r'\[\d{2}:\d{2}(?::\d{2})?\]\s*', '', content)
        except Exception:
            pass

    if not plain_transcript:
        try:
            raw_items = fetch_transcript(video_id)
        except Exception as e:
            err_msg = str(e)
            if "YOUTUBE_IP_BLOCKED" in err_msg:
                return {
                    "status": "network_blocked",
                    "video_id": video_id,
                    "title": title,
                    "author": author,
                    "url": video_url,
                    "message": err_msg.replace("YOUTUBE_IP_BLOCKED:", "").strip()
                }
            elif "NO_TRANSCRIPTS" in err_msg:
                return {
                    "status": "no_subtitles",
                    "video_id": video_id,
                    "title": title,
                    "author": author,
                    "url": video_url,
                    "message": "该视频未提供英文字幕（作者未上传且 YouTube 未生成自动字幕）。"
                }
            else:
                return {
                    "status": "error",
                    "video_id": video_id,
                    "title": title,
                    "author": author,
                    "url": video_url,
                    "message": f"字幕拉取失败: {err_msg}"
                }

        formatted_transcript, plain_transcript = process_raw_transcript(raw_items)
        try:
            with open(raw_file, "w", encoding="utf-8") as f:
                f.write(formatted_transcript)
        except Exception:
            pass

    # 2. 检查暂存区中已有的会话记录，支持无缝“追增挖掘”或“重新挖掘”
    all_staging = load_all_staging_sessions()
    prev_session = all_staging.get("sessions", {}).get(video_id, {})
    prev_candidates = prev_session.get("candidates", []) if prev_session else []

    is_remining = bool(user_query and any(k in user_query for k in ["重新", "重置", "从头", "清空"]))
    exclude_words = []
    if prev_candidates and not is_remining:
        exclude_words = [c.get("word", "") for c in prev_candidates if c.get("word")]

    try:
        new_vocab = mine_c1_c2_vocabulary(title, plain_transcript, user_query, exclude_words=exclude_words)
        if not new_vocab or not isinstance(new_vocab, list):
            raise ValueError("提炼结果非有效列表。")
    except Exception as e:
        return {"status": "error", "message": f"AI 提炼词汇失败: {e}"}

    subdeck_title = sanitize_deck_name(title)
    deck_name = f"YouTube::{subdeck_title}"

    if prev_candidates and not is_remining:
        start_index = len(prev_candidates) + 1
        merged_candidates = prev_candidates + new_vocab
    else:
        start_index = 1
        merged_candidates = new_vocab

    all_staging["sessions"][video_id] = {
        "timestamp": time.time(),
        "meta": meta,
        "deck_name": deck_name,
        "candidates": merged_candidates,
        "raw_count": len(merged_candidates),
    }
    all_staging["latest_video_id"] = video_id
    save_all_staging_sessions(all_staging)

    return {
        "status": "preview",
        "video_id": video_id,
        "title": title,
        "author": author,
        "url": video_url,
        "deck_name": deck_name,
        "raw_count": len(new_vocab),
        "candidates": new_vocab,
        "start_index": start_index,
        "total_staged": len(merged_candidates),
    }


def resolve_confirm_indices(user_input, candidates):
    """
    智能解析用户的自然语言指令，支持：
    1. 纯数字/符号: "1 3 5", "1, 3, 5", "all", "全部", "*"
    2. 自然语言全选: "全部存入", "都存吧", "存进anki", "导入到anki", "收了", "都要"
    3. 词汇原词直接匹配: "存一下 aficionado 和 delineate"
    4. 排除模式: "除了第2个其他都要" / "不要第3个"
    5. 范围与前后数量匹配: "前5个", "后3个", "26到30"
    6. 特征与属性匹配: "只存C2级别的", "把习语存进去"
    7. 离散数字提取: "把第1个和第3个存入Anki"
    """
    text = str(user_input).strip().lower()
    total = len(candidates)
    if total == 0:
        return []

    # 1. 明确的全量信号
    all_signals = ["all", "全部", "全要", "全存", "都存", "都导入", "都要", "全选", "整批", "所有", "*"]
    if any(s == text for s in all_signals) or any(s in text for s in ["全存", "全部存", "全部导入", "都存进", "都存入", "全要了", "全部加入", "都要了", "都存进去", "全部加进去", "都存吧", "全收"]):
        return list(range(1, total + 1))

    # 纯'存入anki'、'导入anki'且无特定数量/序号/词汇指定，默认全量导入
    if re.search(r'^(存入|导入|写入|加到|存进|同步到|写入到)?\s*anki\s*(吗|吧|呢|呀|！|。)?$', text):
        return list(range(1, total + 1))

    # 2. 词汇原词直接匹配 (支持用户直接报词: '存一下 aficionado 和 delineate')
    matched_word_indices = []
    for idx, c in enumerate(candidates, 1):
        w = c.get("word", "").lower().strip()
        if w and w in text:
            matched_word_indices.append(idx)
    if matched_word_indices:
        return sorted(list(set(matched_word_indices)))

    # 3. 排除模式: '除了第2个其他都要' / '不要第3个'
    m_exclude = re.search(r'(?:除了|不要|排除)\s*第?\s*(\d+)\s*个?', text)
    if m_exclude and any(k in text for k in ["其他", "都要", "全要", "其余"]):
        ex_idx = int(m_exclude.group(1))
        return [i for i in range(1, total + 1) if i != ex_idx]

    # 4. 范围与前后数量匹配: '前5个', '后3个', '26到30'
    m_top = re.search(r'前\s*(\d+)\s*个?', text)
    if m_top:
        n = min(int(m_top.group(1)), total)
        return list(range(1, n + 1))

    m_tail = re.search(r'后\s*(\d+)\s*个?', text)
    if m_tail:
        n = min(int(m_tail.group(1)), total)
        return list(range(max(1, total - n + 1), total + 1))

    m_range = re.search(r'(\d+)\s*(?:到|至|-)\s*(\d+)', text)
    if m_range:
        start, end = int(m_range.group(1)), int(m_range.group(2))
        if start > end:
            start, end = end, start
        return [i for i in range(start, end + 1) if 1 <= i <= total]

    # 5. 离散数字匹配: '第1个和第3个', '选 1 3 5', '1, 3, 5'
    nums = [int(n) for n in re.findall(r'\d+', text) if 1 <= int(n) <= total]
    if nums:
        return sorted(list(set(nums)))

    # 6. 特征与属性匹配
    if "c2" in text and "c1" not in text:
        return [i for i, c in enumerate(candidates, 1) if c.get("level") == "C2"]
    if any(k in text for k in ["习语", "idiom", "短语"]):
        return [i for i, c in enumerate(candidates, 1) if any(kw in (c.get("category", "") + c.get("pos", "")) for kw in ["习语", "idiom", "短语", "phr"])]

    return []


def run_confirm(indices_str="all", target_video_id=None):
    all_staging = load_all_staging_sessions()
    sessions = all_staging.get("sessions", {})
    if not sessions:
        return {"status": "error", "message": "未找到待确认的候选词汇暂存会话，请先发送 YouTube 链接进行提炼。"}

    active_vid = None
    if target_video_id and target_video_id in sessions:
        active_vid = target_video_id
    elif all_staging.get("latest_video_id") and all_staging["latest_video_id"] in sessions:
        active_vid = all_staging["latest_video_id"]
    else:
        # Fallback to the newest session by timestamp
        sorted_sessions = sorted(sessions.items(), key=lambda kv: kv[1].get("timestamp", 0), reverse=True)
        active_vid = sorted_sessions[0][0]

    staging = sessions[active_vid]
    meta = staging.get("meta", {})
    deck_name = staging.get("deck_name", "YouTube")
    candidates = staging.get("candidates", [])

    if not candidates:
        if active_vid in all_staging["sessions"]:
            del all_staging["sessions"][active_vid]
            save_all_staging_sessions(all_staging)
        return {"status": "error", "message": f"视频（ID: {active_vid}）的暂存候选词列表为空。"}

    selected_vocab = []
    unselected_vocab = []

    resolved_indices = resolve_confirm_indices(indices_str, candidates)
    if not resolved_indices:
        # 兜底：若包含纯数字提取
        raw_nums = re.findall(r"\d+", str(indices_str))
        resolved_indices = [int(n) for n in raw_nums if 1 <= int(n) <= len(candidates)]

    idxs = set(resolved_indices)
    for i, c in enumerate(candidates, 1):
        if i in idxs:
            selected_vocab.append(c)
        else:
            unselected_vocab.append(c)

    if not selected_vocab:
        return {"status": "error", "message": f"未能从指令（「{indices_str}」）中匹配到有效候选卡片（当前候选范围：1–{len(candidates)}）。您可以直接说「存第1、3个」、「全部存入」、「前5个」或报出具体单词名称。"}

    if unselected_vocab:
        add_known_words([c.get("word", "") for c in unselected_vocab if c.get("word")])

    anki_res = inject_into_anki(deck_name, selected_vocab)
    note_file = save_obsidian_study_note(meta, selected_vocab, deck_name, anki_res)
    vmark_mounted = mount_to_vmark(note_file)

    # Mark confirmed in staging session (preserve for context continuity)
    if active_vid in all_staging["sessions"]:
        s_entry = all_staging["sessions"][active_vid]
        s_entry["confirmed_at"] = time.time()
        s_entry["status"] = "confirmed"
        prev_confirmed = set(s_entry.get("confirmed_indices", []))
        s_entry["confirmed_indices"] = sorted(list(prev_confirmed | set(resolved_indices)))
    save_all_staging_sessions(all_staging)

    return {
        "status": "success",
        "video_id": meta.get("video_id", active_vid),
        "title": meta.get("title"),
        "author": meta.get("author"),
        "url": meta.get("url"),
        "deck_name": deck_name,
        "selected_count": len(selected_vocab),
        "unselected_count": len(unselected_vocab),
        "anki_added": anki_res.get("added", 0),
        "anki_skipped": anki_res.get("skipped", 0),
        "anki_success": anki_res.get("success", False),
        "anki_synced": anki_res.get("synced", False),
        "anki_error": anki_res.get("error"),
        "note_file": note_file,
        "vmark_mounted": vmark_mounted,
        "selected_vocab": selected_vocab,
    }


def main():
    parser = argparse.ArgumentParser(description="YouTube Anki Vocabulary Miner")
    parser.add_argument("url", nargs="?", default=None, help="YouTube URL or Video ID")
    parser.add_argument("-q", "--query", type=str, default=None, help="User specific requirement")
    parser.add_argument("--preview", action="store_true", help="Stage candidates and output preview without injecting into Anki")
    parser.add_argument("--confirm", nargs="?", const="all", default=None, help="Confirm candidates into Anki (e.g. '1 3 5' or 'all')")
    parser.add_argument("--video-id", type=str, default=None, help="Target specific video ID in staging sessions")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    args = parser.parse_args()

    if args.confirm is not None:
        result = run_confirm(args.confirm, target_video_id=args.video_id)
    elif args.preview:
        if not args.url:
            err = {"status": "error", "message": "必须提供 YouTube URL 才能执行 --preview"}
            print(json.dumps(err, ensure_ascii=False)) if args.json else print("[ERROR] 必须提供 YouTube URL", file=sys.stderr)
            sys.exit(1)
        result = run_preview(args.url, args.query)
    else:
        if not args.url:
            parser.print_help()
            sys.exit(1)
        result = run_pipeline(args.url, args.query)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        if result.get("status") == "preview":
            print(f"[PREVIEW] 视频标题: {result['title']}")
            print(f"[CHANNEL] 频道主创: {result['author']}")
            print(f"\n[CANDIDATES] 待选高阶表达（共 {len(result['candidates'])} 项）:")
            for i, item in enumerate(result.get("candidates", []), 1):
                cat = item.get("category", item.get("level", "C1"))
                print(f"  {i}. {item['word']} {item['ipa']} ({cat} {item['pos']}) - {item['definition_zh']}")
                print(f"     Context: {item['context_sentence']}")
        elif result.get("status") == "success":
            print(f"[VIDEO] 视频标题: {result['title']}")
            print(f"[CHANNEL] 频道主创: {result['author']}")
            print(f"[DECK] Anki 牌组: {result['deck_name']}")
            print(f"[CARDS] 新增卡片: {result['anki_added']} 张 (跳过重复 {result['anki_skipped']} 张)")
            print(f"[NOTE] 笔记落盘: {result['note_file']}")
            print(f"[VMARK] VMark 挂载: {'已挂载' if result['vmark_mounted'] else '未运行'}")
            print("\n[VOCAB] 入库词汇列表:")
            for item in result.get("selected_vocab", result.get("top_vocab", [])):
                print(f"  • {item['word']} {item['ipa']} ({item['level']} {item['pos']}) - {item['definition_zh']}")
                print(f"    Context: {item['context_sentence']}")
        elif result.get("status") == "no_subtitles":
            print(f"[WARN] 字幕缺失: {result.get('message')}", file=sys.stderr)
            sys.exit(0)
        elif result.get("status") == "network_blocked":
            print(f"[WARN] 代理受阻: {result.get('message')}", file=sys.stderr)
            sys.exit(0)
        else:
            print(f"[ERROR] 运行失败: {result.get('message')}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()

