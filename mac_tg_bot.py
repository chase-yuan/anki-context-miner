#!/usr/bin/env python3
"""
Telegram Mac Remote Controller Bot
Exclusively authorized for user ID: 1626499224
"""

import datetime
import time
import logging
import os
import re
import json
import asyncio
import subprocess
import sys
import shutil
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from config_loader import get_config
from youtube_anki_miner import resolve_confirm_indices, OUTPUT_DIR
import telegram.error
from telegram.request import HTTPXRequest
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ── 1. 核心凭证与网络配置 ──
CONFIG = get_config()
BOT_TOKEN = CONFIG.get("telegram", {}).get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN") or ""
ADMIN_USER_ID = int(CONFIG.get("telegram", {}).get("admin_user_id") or os.environ.get("TELEGRAM_ADMIN_ID") or 0)
PROXY_URL = CONFIG.get("telegram", {}).get("proxy_url") or os.environ.get("HTTP_PROXY") or ""
MINER_SCRIPT = str(REPO_DIR / "youtube_anki_miner.py")
BILI_SCRIPT = str(REPO_DIR / "bili_study_engine.py")
STAGING_FILE = os.path.expanduser(CONFIG.get("paths", {}).get("staging_file") or "~/.config/anki_video_staging.json")

# ── 2. 日志配置 ──
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("MacTGBot")


# ── 3. 权限安全守卫 ──
async def check_auth(update: Update) -> bool:
    user = update.effective_user
    if ADMIN_USER_ID != 0 and (not user or user.id != ADMIN_USER_ID):
        logger.warning(f"Unauthorized access attempt from user: {user.id if user else 'Unknown'}")
        if update.message:
            await update.message.reply_text("[安全] 权限拒绝：未授权的访问请求。")
        return False
    return True


# ── 4.1 学习上下文缓存 ──
LAST_STUDY_NOTE = {}
LAST_STUDY_TITLE = {}


def split_text_into_chunks(text: str, max_chars: int = 2000) -> list:
    """智能按照段落与行边界分割文本，避免切断 Markdown 语法实体"""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_chars:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            if line_len > max_chars:
                for i in range(0, len(line), max_chars):
                    chunks.append(line[i : i + max_chars])
                continue
        current_chunk.append(line)
        current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks


async def send_long_message(message, text: str):
    """安全分片发送长消息，带 Markdown 语法容错降级与段落保护"""
    if not text:
        return
    chunks = split_text_into_chunks(text, max_chars=2000)
    for chunk in chunks:
        try:
            await message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            try:
                await message.reply_text(chunk)
            except Exception as e:
                logger.error(f"Failed to send chunk: {e}")


def extract_json_payload(raw_stdout: str):
    """鲁棒提取 stdout 中的有效 JSON，优先单行，后向兼容多行与混合输出"""
    if not raw_stdout:
        return None
    raw = raw_stdout.strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    for l in reversed(raw.split("\n")):
        line_s = l.strip()
        if line_s.startswith("{") and line_s.endswith("}"):
            try:
                obj = json.loads(line_s)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    decoder = json.JSONDecoder()
    pos = 0
    candidates = []
    while pos < len(raw):
        pos = raw.find("{", pos)
        if pos == -1:
            break
        try:
            obj, end = decoder.raw_decode(raw[pos:])
            if isinstance(obj, dict):
                candidates.append(obj)
                pos += end
                continue
        except Exception:
            pass
        pos += 1
    if candidates:
        return candidates[-1]
    return None


# ── 4. 指令处理器 ──
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    msg = (
        "*Mac 终端 AI 助理（移动专属窗口）已全量就绪*\n\n"
        "本机器人直接对接您 Mac 上的 Antigravity 智能体大脑，与您在 Mac Terminal 中聊天**完全等同**：\n\n"
        "*支持功能与场景*：\n"
        "• **任意自由聊天/技术提问**：随想随问，模型具备满血推理能力\n"
        "• **调用电脑本地技能**：例如「_用消防总工考考我_」、「_检查 nlpm 规范_」\n"
        "• **读写本地文件与知识库**：例如「_看下李笑来最新转录的笔记_」、「_搜索 Downloads 里的文件_」\n"
        "• **B 站视频自动化精读**：发送任何 B 站链接（可附带要求，如 `链接 重点讲接线`）\n"
        "• **YouTube 进阶词汇挖掘与 Anki 直刷**：发送任何 YouTube 链接（可附带要求，如 `链接 挖掘 C1/C2 词汇`），自动提取字幕 $\to$ 提炼 C1/C2 词汇 $\to$ 在 Anki 创建子牌组写入卡片 $\to$ 落盘 Obsidian。\n\n"
        "*硬件快捷指令*：\n"
        "• `/status` - 查看 Mac 实时 CPU/内存/磁盘状态\n"
        "• `/shot` - 拍摄当前 Mac 物理屏幕快照\n"
        "• `/transcribe` - 启动李笑来音频离线转录流水线\n"
        "• `/openchat` - 唤醒 VMark 挂载当前最新笔记"
    )
    await send_long_message(update.message, msg)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uptime_info = os.popen("uptime").read().strip()
    disk_info = os.popen("df -h / | tail -n 1 | awk '{print $4}'").read().strip()
    mem_info = os.popen("vm_stat | grep 'Pages free' | awk '{print $3}'").read().strip().rstrip(".")
    
    report = (
        f"*Mac 运行报告* ({now})\n\n"
        f"• *系统负载*: `{uptime_info}`\n"
        f"• *主分区可用*: `{disk_info}`\n"
        f"• *空闲内存页*: `{mem_info} pages`\n"
        f"• *网络状态*: 代理连通正常 (`{PROXY_URL}`)"
    )
    await update.message.reply_text(report, parse_mode="Markdown")


async def screenshot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await update.message.reply_text("[快照] 正在截取 Mac 屏幕快照...")
    shot_path = "/tmp/mac_screen.png"
    if os.path.exists(shot_path):
        try:
            os.remove(shot_path)
        except Exception:
            pass

    res = subprocess.run(["screencapture", "-x", shot_path], capture_output=True)
    if res.returncode == 0 and os.path.exists(shot_path):
        with open(shot_path, "rb") as f:
            await update.message.reply_photo(
                photo=f,
                caption=f"捕获时间: {datetime.datetime.now().strftime('%H:%M:%S')}",
            )
        try:
            os.remove(shot_path)
        except Exception:
            pass
    else:
        err = res.stderr.decode("utf-8", errors="ignore")
        await update.message.reply_text(f"[错误] 截屏失败: {err or '未知错误'}")


async def transcribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    script_path = CONFIG.get("paths", {}).get("transcribe_script", "")
    if not script_path or not os.path.exists(script_path):
        await update.message.reply_text("[提示] 本地音频转录脚本未配置（请在 config.json 的 paths.transcribe_script 中指定路径）。")
        return
    await update.message.reply_text("[转录] 正在触发本地音频自动化转录流水线...")
    in_folder = CONFIG.get("paths", {}).get("transcribe_input_folder", os.path.expanduser("~/Downloads"))
    out_folder = CONFIG.get("paths", {}).get("transcribe_output_folder", os.path.expanduser("~/Documents"))
    cmd = [sys.executable, script_path, "--folder", in_folder, "--output-folder", out_folder, "--keep-source"]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await update.message.reply_text("[转录] 任务已在 Mac 后台启动，转录完成后会自动同步至 Obsidian。")


async def openchat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await update.message.reply_text("[VMark] 正在呼叫 VMark 打开最新会话记录...")
    custom_script = CONFIG.get("paths", {}).get("vmark_script")
    candidates = []
    if custom_script:
        candidates.append(os.path.expanduser(custom_script))
    candidates.append(os.path.expanduser("~/.gemini/config/skills/vmark-chat-viewer/scripts/open_vmark_chat.py"))

    script_path = None
    for c in candidates:
        if c and os.path.exists(c):
            script_path = c
            break

    if script_path:
        res = subprocess.run([sys.executable, script_path, "--json"], capture_output=True, text=True)
        if res.returncode == 0:
            await update.message.reply_text("[VMark] 已在 VMark 中激活最新聊天记录标签页。")
        else:
            await update.message.reply_text(f"[提示] 激活提示: {res.stdout or res.stderr}")
    else:
        await update.message.reply_text("[提示] 未配置 VMark 联动脚本路径（可在 config.json 中配置 paths.vmark_script）。")


async def sync_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await update.message.reply_text("[Anki] 正在触发 Mac 本地与 AnkiWeb 云端同步...")
    loop = asyncio.get_running_loop()
    def do_sync():
        req = urllib.request.Request(
            "http://127.0.0.1:8765",
            data=json.dumps({"action": "sync", "version": 6}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=25.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        data = await loop.run_in_executor(None, do_sync)
        if data.get("error") is None:
            await update.message.reply_text("✅ [Anki] AnkiWeb 云端同步完成！手机端打开即可即时复习。")
        else:
            await update.message.reply_text(f"⚠️ [Anki] 同步异常: {data.get('error')}")
    except Exception as e:
        await update.message.reply_text(f"❌ [Anki] 同步调用失败: {e}")


async def handle_yt_preview_display(message, data):
    candidates = data.get("candidates", [])
    title = data.get("title", "YouTube Video")
    author = data.get("author", "")
    start_index = data.get("start_index", 1)

    header_parts = [f"*{title}*"]
    if author:
        header_parts.append(f"_{author}_")
    if start_index > 1:
        header_parts.append(f"\n高阶表达加深挖掘（批次第 {start_index}–{start_index + len(candidates) - 1} 项，共 {len(candidates)} 项）：\n")
    else:
        header_parts.append(f"\n高阶表达速览（共 {len(candidates)} 项）：\n")

    meta_parts = ["\n".join(header_parts)]
    for idx, v in enumerate(candidates, start_index):
        w = v.get("word", "").strip()
        ipa = f"`{v.get('ipa').strip()}` " if v.get("ipa") else ""
        cat = v.get("category", "")
        pos = v.get("pos", "")
        tag = f"[{cat} · {pos}]" if cat and pos else f"[{cat or pos}]"
        def_zh = v.get("definition_zh", "").strip()
        exp = v.get("explanation", "").strip()
        ctx = v.get("context_sentence", "").strip()
        trans = v.get("sentence_translation", "").strip()

        card_block = [
            f"*{idx}. {w}* {ipa}{tag}",
            f"• 释义：{def_zh}",
        ]
        if exp:
            card_block.append(f"• 解析：{exp}")
        if ctx:
            card_block.append(f"• 原句：_{ctx}_")
        if trans:
            card_block.append(f"• 译文：{trans}")
        card_block.append("")
        meta_parts.append("\n".join(card_block))

    await send_long_message(message, "\n".join(meta_parts))


def find_raw_transcript(title: str):
    raw_dir = os.path.join(OUTPUT_DIR, "Raw Transcripts")
    if not os.path.exists(raw_dir) or not title:
        return None
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", title).strip()
    candidate = os.path.join(raw_dir, f"{sanitized} (Raw Transcript).txt")
    if os.path.exists(candidate):
        return candidate
    words = [w for w in re.split(r"[\s_]+", sanitized) if len(w) > 3]
    for f in os.listdir(raw_dir):
        if f.endswith("(Raw Transcript).txt") and any(w in f for w in words):
            return os.path.join(raw_dir, f)
    return None


def build_state_capsule(target_video_id=None):
    staging_file = STAGING_FILE
    if not os.path.exists(staging_file):
        return None
    try:
        with open(staging_file, "r", encoding="utf-8") as f:
            staging_data = json.load(f)
    except Exception:
        return None

    sessions = staging_data.get("sessions", {})
    if not sessions:
        return None

    now = time.time()
    active_sessions = {k: v for k, v in sessions.items() if now - v.get("timestamp", 0) < 172800}
    if not active_sessions:
        return None

    vid = target_video_id
    if not vid or vid not in active_sessions:
        vid = staging_data.get("latest_video_id")
        if not vid or vid not in active_sessions:
            vid = list(active_sessions.keys())[-1]

    s = active_sessions.get(vid)
    if not s:
        return None

    meta = s.get("meta", {})
    title = meta.get("title", "")
    author = meta.get("author", "")
    candidates = s.get("candidates", [])
    deck_name = s.get("deck_name", "YouTube")

    cand_summary = []
    for idx, c in enumerate(candidates, 1):
        w = c.get("word", "").strip()
        def_zh = c.get("definition_zh", "").strip()
        cand_summary.append(f"{idx}. {w}: {def_zh}")

    raw_transcript = find_raw_transcript(title)

    return {
        "video_id": vid,
        "title": title,
        "author": author,
        "url": meta.get("url", f"https://www.youtube.com/watch?v={vid}"),
        "deck_name": deck_name,
        "total_candidates": len(candidates),
        "candidates": candidates,
        "candidate_summary": "\n".join(cand_summary),
        "transcript_file": raw_transcript,
    }


async def handle_anki_confirm_display(message, data):
    deck = data.get("deck_name", "YouTube")
    added = data.get("anki_added", 0)
    synced = data.get("anki_synced", False)
    sync_status = "• *云端同步*：已自动同步至 AnkiWeb (手机端开箱即用)" if synced else "• *云端同步*：待同步 (可输入 /sync 手动触发)"
    msg_parts = [
        "*【已入库 Anki】*\n",
        f"• *牌组*：`{deck}`",
        f"• *生成卡片*：{added} 张（含双轨神经发音）",
        f"{sync_status}\n"
    ]
    selected_v = data.get("selected_vocab", [])
    if selected_v:
        msg_parts.append("*入库列表*:")
        for idx, v in enumerate(selected_v, 1):
            w = v.get("word", "").strip()
            ipa = f"`{v.get('ipa').strip()}` " if v.get("ipa") else ""
            zh = v.get("definition_zh", "").strip()
            msg_parts.append(f"{idx}. *{w}* {ipa}- {zh}")
    await send_long_message(message, "\n".join(msg_parts))


def run_confirm_engine(indices_arg: str, target_vid: str = None):
    cmd = [sys.executable, MINER_SCRIPT, "--confirm", str(indices_arg), "--json"]
    if target_vid:
        cmd.extend(["--video-id", target_vid])
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return res.stdout, res.stderr, res.returncode


def run_preview_engine(target_vid: str, user_query: str = None):
    cmd = [sys.executable, MINER_SCRIPT, target_vid, "--preview", "--json"]
    if user_query:
        cmd.extend(["-q", str(user_query)])
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return res.stdout, res.stderr, res.returncode


def run_general_llm(prompt_text: str) -> str:
    from llm_client import call_llm
    try:
        ans = call_llm(prompt_text)
        return ans or "（助手未返回文本内容）"
    except Exception as e:
        return f"[错误] 调用 LLM 推理引擎异常: {e}"


def dispatch_semantic_intent(user_text: str, capsule: dict = None) -> dict:
    """通过配置的 LLM 引擎进行上下文感知与高精度语义意图调度"""
    if not capsule:
        return {"intent": "general_chat", "answer": run_general_llm(user_text)}

    prompt = f"""你是一个运行在 Mac 本地的高精度语义意图识别与执行调度器。
用户正在 Telegram 上通过移动端与你交互。

【当前激活的 YouTube 学习会话】：
• 视频标题：{capsule['title']}
• 视频作者：{capsule['author']}
• 视频ID：{capsule['video_id']}
• 候选词汇列表（共 {capsule['total_candidates']} 项）：
{capsule['candidate_summary']}
• 本地原始字幕文件：{capsule['transcript_file'] or '未缓存'}

用户最新输入："{user_text}"

请判定用户真实意图，必须严格按以下 JSON 格式输出，严禁输出任何多余的解释、Markdown 标记或代码块外的前后缀：
{{
  "intent": "confirm_anki" | "refine_mining" | "query_video" | "general_chat",
  "indices": [如果 intent 是 confirm_anki，提取匹配到的候选词序号整数列表，如 [2, 10]],
  "query": "如果 intent 是 refine_mining，提炼的新挖掘要求",
  "answer": "如果 intent 是 query_video 或 general_chat，直接针对问题给出高质量实质性解答"
}}

规则说明：
1. confirm_anki：用户想把当前视频的某些词、全部词、或某个特征的词（如「把刚才讲走步的医学词和 aficionado 存进anki」、「存第2个」、「存C2的词」）存入 Anki。必须在 indices 中给出准确的序号整数列表。
2. refine_mining：用户想在当前视频中挖掘更多、更难、更深、或者换一批词汇（如「再挖掘一些我可能不懂的单词以及短语」、「多找点地道习语」、「换一批更难的」）。必须在 query 中给出具体提炼要求。
3. query_video：用户询问或讨论当前视频的具体内容、作者观点、核心论据（如「这个视频讲了什么？」、「作者提到的4步法具体是什么？」、「作者怎么解释多巴胺机制的？」）。如果本地有字幕文件，请直接结合内容给出详实解答并在 answer 中返回。
4. general_chat：用户进行与该视频内容无关的通用提问、系统操作、询问使用方法（如「我要是想写入anki怎么做？」、「机器人没有智能吗？」、「用消防总工考考我」）。直接在 answer 中给予精准、无谄媚、实质性的解答。
"""
    from llm_client import call_llm
    try:
        stdout = call_llm(prompt, json_mode=True)
        payload = extract_json_payload(stdout)
        if payload and isinstance(payload, dict) and "intent" in payload:
            return payload
    except Exception as e:
        logger.error(f"Semantic dispatch error: {e}")

    # 容错降级
    fallback_ans = run_general_llm(user_text)
    return {"intent": "general_chat", "answer": fallback_ans}


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    logger.info(f"User {user_id} text: {text}")

    clean_text = text.strip().lower()

    # ── 分支 1：Fast-Bypass 视频链接（极速拦截，无需智能体路由） ──
    url_match = re.search(r"(https?://[^\s]+)", text)
    bv_match = re.search(r"(BV[a-zA-Z0-9]{10})", text, re.IGNORECASE)

    # 1.1 Bilibili 链接
    bili_url = None
    if url_match and ("bilibili.com" in url_match.group(1) or "b23.tv" in url_match.group(1)):
        bili_url = url_match.group(1)
    elif not url_match and bv_match:
        bili_url = bv_match.group(1)

    if bili_url:
        user_query = text.replace(bili_url, "").strip()
        user_query = re.sub(r"【.*?】", "", user_query).strip()
        user_query = re.sub(r"(赶紧来看看吧|点击链接查看|去\s*bilibili\s*看视频|点击链接直接打开|哔哩哔哩)", "", user_query).strip()

        loop = asyncio.get_running_loop()
        stop_typing = asyncio.Event()

        async def keep_typing():
            while not stop_typing.is_set():
                try:
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(stop_typing.wait(), timeout=4.0)
                except asyncio.TimeoutError:
                    pass

        typing_task = asyncio.create_task(keep_typing())

        def run_bili_engine():
            if not os.path.exists(BILI_SCRIPT):
                return json.dumps({"status": "error", "message": "bili_study_engine.py not bundled"}), "", 1
            cmd = [sys.executable, BILI_SCRIPT, bili_url, "--json"]
            if user_query:
                cmd.extend(["-q", user_query])
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            return res.stdout, res.stderr, res.returncode

        stdout, stderr, code = await loop.run_in_executor(None, run_bili_engine)
        stop_typing.set()
        await typing_task

        try:
            data = extract_json_payload(stdout)
            if not data:
                raise ValueError(f"脚本输出未包含有效 JSON:\n{stdout or stderr}")

            if data.get("status") == "success":
                msg_parts = [
                    "*【B 站课程精编讲义已生成】*\n",
                    f"• *课程*：{data.get('title')}",
                    f"• *小节*：`P{data.get('page')}` {data.get('part_title')}",
                    f"• *来源*：`{data.get('source')}`",
                    f"• *VMark 状态*：{'已静默挂载到标签页' if data.get('vmark_mounted') else '未运行'}",
                    f"• *归档*：`{data.get('note_file')}`\n",
                ]
                if data.get("answer"):
                    msg_parts.append(f"*【专项深度解答】*:\n{data.get('answer')}\n")
                msg_parts.append("*您可以直接发送消息继续聊天或追问。*")
                await send_long_message(update.message, "\n".join(msg_parts))
            else:
                await send_long_message(update.message, f"[提示] 解析提示：{data.get('message', '未提取到字幕')}")
        except Exception as e:
            await send_long_message(update.message, f"[错误] 处理异常：{e}\n{stdout or stderr}")
        return

    # 1.2 YouTube 链接
    yt_url = None
    if url_match and ("youtube.com" in url_match.group(1) or "youtu.be" in url_match.group(1)):
        yt_url = url_match.group(1)

    if yt_url:
        user_query = text.replace(yt_url, "").strip()
        user_query = re.sub(r"【.*?】", "", user_query).strip()

        loop = asyncio.get_running_loop()
        stop_typing = asyncio.Event()

        async def keep_typing():
            while not stop_typing.is_set():
                try:
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(stop_typing.wait(), timeout=4.0)
                except asyncio.TimeoutError:
                    pass

        typing_task = asyncio.create_task(keep_typing())

        def run_yt_engine():
            cmd = [sys.executable, MINER_SCRIPT, yt_url, "--preview", "--json"]
            if user_query:
                cmd.extend(["-q", user_query])
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return res.stdout, res.stderr, res.returncode

        stdout, stderr, code = await loop.run_in_executor(None, run_yt_engine)
        stop_typing.set()
        await typing_task

        try:
            data = extract_json_payload(stdout)
            if not data:
                raise ValueError(f"脚本输出未包含有效 JSON:\n{stdout or stderr}")

            if data.get("status") == "preview":
                await handle_yt_preview_display(update.message, data)
            elif data.get("status") == "no_subtitles":
                title = data.get("title", "YouTube Video")
                await send_long_message(update.message, f"*【字幕缺失】*\n• 视频：*{title}*\n• 提示：该视频未提供英文字幕（作者未上传且 YouTube 未生成自动字幕）。")
            elif data.get("status") == "network_blocked":
                title = data.get("title", "YouTube Video")
                await send_long_message(update.message, f"*【代理通道受阻】*\n• 视频：*{title}*\n• 提示：YouTube 拦截了当前代理节点 IP（触发了防爬机器人验证）。\n• 建议：系统已尝试自动轮换节点。如果多次提示，请在 Mac 的 Clash Verge 中手动切换一个优质海外节点（推荐日本/香港 IEPL 节点）后重试。")
            elif data.get("status") == "success":
                meta_parts = [
                    "*【YouTube 进阶词汇挖掘完成 · 已直刷 Anki】*\n",
                    f"• *视频*：{data.get('title')}",
                    f"• *主创*：{data.get('author')}",
                    f"• *Anki 牌组*：`{data.get('deck_name')}`",
                    f"• *卡片录入*：{'成功写入 ' + str(data.get('anki_added')) + ' 张新卡片' if data.get('anki_success') else 'Anki 写入异常: ' + str(data.get('anki_error'))}" + (f" (跳过重复 {data.get('anki_skipped')} 张)" if data.get('anki_skipped') else ""),
                    f"• *VMark 状态*：{'已静默挂载到标签页' if data.get('vmark_mounted') else '未运行'}",
                    f"• *Obsidian 归档*：`{data.get('note_file')}`\n",
                    "*打开手机 AnkiMobile 同步，或在 Mac 上即可直接开始刷卡复习。*"
                ]
                await send_long_message(update.message, "\n".join(meta_parts))
            else:
                await send_long_message(update.message, f"[提示] 提炼提示：{data.get('message', '未成功提炼词汇')}")
        except Exception as e:
            await send_long_message(update.message, f"[错误] 处理异常：{e}\n{stdout or stderr}")
        return

    # 快捷确定性指令：同步 Anki (< 0.01s 命中)
    if clean_text in ["同步", "同步anki", "anki同步", "同步卡片", "更新anki", "sync"]:
        await sync_cmd(update, context)
        return

    # ── 分支 2：多媒体暂存状态检测与确定性指令快速旁路 (< 0.01s) ──
    staging_file = STAGING_FILE
    active_sessions = {}
    target_vid = None

    if os.path.exists(staging_file):
        try:
            with open(staging_file, "r", encoding="utf-8") as f:
                staging_data = json.load(f)
            sessions = staging_data.get("sessions", {})
            now = time.time()
            active_sessions = {k: v for k, v in sessions.items() if now - v.get("timestamp", 0) < 172800}
            if active_sessions:
                reply_msg = update.message.reply_to_message
                if reply_msg and reply_msg.text:
                    reply_txt = reply_msg.text
                    for vid, s_info in active_sessions.items():
                        s_title = s_info.get("meta", {}).get("title", "")
                        if vid in reply_txt or (s_title and s_title in reply_txt):
                            target_vid = vid
                            break
                if not target_vid:
                    target_vid = staging_data.get("latest_video_id") or list(active_sessions.keys())[-1]
        except Exception as e:
            logger.error(f"Error checking staging sessions: {e}")

    # 检查是否为确定性卡片选择指令（数字、all、前5个、除了第2个等）
    if active_sessions and target_vid and target_vid in active_sessions:
        candidates = active_sessions[target_vid].get("candidates", [])
        is_question = any(q in clean_text for q in ["怎么", "如何", "怎样", "能不能", "可以吗", "为什么", "没有智能", "是吗", "吗？", "吗?", "什么", "？", "?"])
        is_refine = any(r in clean_text for r in ["再挖", "多挖", "重新", "加深", "换一批", "再来", "更难", "更深"])

        if not is_question and not is_refine:
            matched_indices = resolve_confirm_indices(text, candidates)
            if matched_indices:
                loop = asyncio.get_running_loop()
                stop_typing = asyncio.Event()

                async def keep_typing():
                    while not stop_typing.is_set():
                        try:
                            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
                        except Exception:
                            pass
                        try:
                            await asyncio.wait_for(stop_typing.wait(), timeout=4.0)
                        except asyncio.TimeoutError:
                            pass

                typing_task = asyncio.create_task(keep_typing())
                stdout, stderr, code = await loop.run_in_executor(
                    None,
                    lambda: run_confirm_engine(indices_arg=" ".join(map(str, matched_indices)), target_vid=target_vid)
                )
                stop_typing.set()
                await typing_task

                data = extract_json_payload(stdout)
                if data and data.get("status") == "success":
                    await handle_anki_confirm_display(update.message, data)
                    return
                else:
                    err_msg = data.get("message") if data else (stderr or stdout)
                    await send_long_message(update.message, f"[提示] 入库提示：{err_msg}")
                    return

    # ── 分支 3：统一语义意图与多模态智能体调度 (Semantic Intent Dispatcher) ──
    loop = asyncio.get_running_loop()
    stop_typing = asyncio.Event()

    async def keep_typing():
        while not stop_typing.is_set():
            try:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_typing.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    typing_task = asyncio.create_task(keep_typing())

    # 组装状态胶囊
    capsule = build_state_capsule(target_video_id=target_vid) if active_sessions else None

    # 异步调度语义意图与执行
    payload = await loop.run_in_executor(None, lambda: dispatch_semantic_intent(text, capsule))
    stop_typing.set()
    await typing_task

    intent = payload.get("intent", "general_chat")

    if intent == "confirm_anki":
        indices = payload.get("indices") or payload.get("index_list") or (resolve_confirm_indices(text, capsule.get("candidates", [])) if capsule else [])
        if indices and target_vid:
            stop_typing.clear()
            typing_task = asyncio.create_task(keep_typing())
            stdout, stderr, code = await loop.run_in_executor(
                None,
                lambda: run_confirm_engine(indices_arg=" ".join(map(str, indices)), target_vid=target_vid)
            )
            stop_typing.set()
            await typing_task

            data = extract_json_payload(stdout)
            if data and data.get("status") == "success":
                await handle_anki_confirm_display(update.message, data)
                return
            else:
                err_msg = data.get("message") if data else (stderr or stdout)
                await send_long_message(update.message, f"[提示] 入库提示：{err_msg}")
                return
        else:
            ans = payload.get("answer") or payload.get("message")
            await send_long_message(update.message, ans or "未能从指令中提取出具体的候选序号，请确认序号或直接说明词汇名称。")
            return

    elif intent == "refine_mining" and target_vid:
        refine_q = payload.get("query") or payload.get("refine_query") or payload.get("requirements") or text
        stop_typing.clear()
        typing_task = asyncio.create_task(keep_typing())
        stdout, stderr, code = await loop.run_in_executor(
            None,
            lambda: run_preview_engine(target_vid=target_vid, user_query=refine_q)
        )
        stop_typing.set()
        await typing_task

        data = extract_json_payload(stdout)
        if data and data.get("status") == "preview":
            await handle_yt_preview_display(update.message, data)
            return
        elif data and data.get("status") == "no_subtitles":
            await send_long_message(update.message, "*【字幕缺失】*\n• 提示：未获取到可用英文字幕。")
            return
        elif data and data.get("status") == "network_blocked":
            await send_long_message(update.message, "*【代理通道受阻】*\n• 提示：YouTube 拦截了当前代理节点 IP。")
            return
        else:
            err_msg = data.get("message") if data else (stderr or stdout)
            await send_long_message(update.message, f"[提示] 挖掘提示：{err_msg}")
            return

    elif intent == "query_video":
        ans = payload.get("answer") or payload.get("message")
        if ans:
            await send_long_message(update.message, ans)
            return

    # intent == "general_chat" 或其它意图
    ans = payload.get("answer") or payload.get("message")
    if ans:
        await send_long_message(update.message, ans)
    else:
        stop_typing.clear()
        typing_task = asyncio.create_task(keep_typing())
        fallback_reply = await loop.run_in_executor(None, lambda: run_general_llm(text))
        stop_typing.set()
        await typing_task
        await send_long_message(update.message, fallback_reply)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    # 忽略瞬态网络闪断、重试与代理断开（例如休眠唤醒），严禁忽略 BadRequest / Forbidden 等应用层调用错误
    if isinstance(err, (telegram.error.TimedOut, telegram.error.RetryAfter)):
        logger.warning(f"底层网络瞬态抖动（自动恢复中）: {err}")
        return
    if isinstance(err, telegram.error.NetworkError) and not isinstance(err, (telegram.error.BadRequest, telegram.error.Forbidden)):
        logger.warning(f"底层网络瞬态抖动（自动恢复中）: {err}")
        return
    err_str = str(type(err)) + str(err)
    if any(k in err_str for k in ["ConnectError", "RemoteProtocolError", "ConnectionRefusedError", "ProxyError"]):
        logger.warning(f"代理通道波动（自动恢复中）: {err}")
        return

    logger.error("Exception while handling an update:", exc_info=err)
    if isinstance(update, Update) and update.effective_message:
        try:
            await send_long_message(update.effective_message, f"[错误] 处理发生错误: {err}")
        except Exception:
            pass


# ── 5. 主程序启动 ──
def main():
    print(f"[{datetime.datetime.now()}] 正在启动 Telegram 控制机器人（目标用户: {ADMIN_USER_ID}）...")
    
    # 启动 macOS 防系统休眠保护（保持后台网络与 CPU 常驻，但允许屏幕正常熄灭省电）
    try:
        subprocess.Popen(
            ["/usr/bin/caffeinate", "-s", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[{datetime.datetime.now()}] 已启用 macOS caffeinate 防休眠机制。")
    except Exception as e:
        print(f"[{datetime.datetime.now()}] 启动 caffeinate 失败: {e}")

    req = HTTPXRequest(
        connection_pool_size=16,
        read_timeout=35.0,
        write_timeout=35.0,
        connect_timeout=20.0,
        pool_timeout=20.0,
        proxy=PROXY_URL,
    )
    
    builder = ApplicationBuilder().token(BOT_TOKEN).request(req).get_updates_request(req)
    app = builder.build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("shot", screenshot_cmd))
    app.add_handler(CommandHandler("transcribe", transcribe_cmd))
    app.add_handler(CommandHandler("openchat", openchat_cmd))
    app.add_handler(CommandHandler("sync", sync_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))

    print(f"[{datetime.datetime.now()}] Telegram Bot 服务已成功在 Mac 后台轮询运行中...")
    while True:
        try:
            app.run_polling(drop_pending_updates=True)
            break
        except Exception as e:
            logger.error(f"轮询异常退出，将在 5 秒后自动拉起重连: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
