#!/usr/bin/env python3
"""
Bilibili Study Engine (B 站视频自动化学习精编引擎)
Option B Implementation:
Direct URL Ingestion -> Subtitle Resolution (API + Smart Local Sniff) -> Obsidian Archival -> Silent VMark Mount
"""

import os
import sys
import re
import glob
import json
import time
import argparse
import urllib.request
import urllib.parse
import gzip
import subprocess
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))
OBSIDIAN_VAULT = os.path.expanduser("~/Vault/MyObsidian")
DOWNLOADS_DIR = os.path.expanduser("~/Downloads")
COOKIE_FILE = os.path.expanduser("~/.config/bilibili/cookie.txt")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Accept-Encoding": "gzip",
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
    viewer_dir = os.path.expanduser("~/.gemini/config/skills/vmark-chat-viewer/scripts")
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

def parse_bilibili_url(url_or_bvid):
    raw = url_or_bvid.strip()
    url_match = re.search(r"(https?://[^\s]+)", raw)
    target = url_match.group(1) if url_match else raw
    
    # 解析 b23.tv 短链重定向
    if "b23.tv" in target:
        req = urllib.request.Request(
            target,
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                target = resp.geturl()
        except urllib.error.HTTPError as e:
            if "Location" in e.headers:
                target = e.headers["Location"]
        except Exception:
            pass

    bvid_match = re.search(r"(BV[a-zA-Z0-9]{10})", target, re.IGNORECASE)
    if not bvid_match:
        raise ValueError(f"无法从输入中解析出有效的 B 站 BV 号: {raw}")
    bvid = bvid_match.group(1)
    
    # 解析分P参数 ?p=
    p_match = re.search(r"[?&]p=(\d+)", target)
    page_num = int(p_match.group(1)) if p_match else 1
    return bvid, page_num

def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                cookie_content = f.read().strip()
                if cookie_content:
                    req.add_header("Cookie", cookie_content)
        except Exception:
            pass

    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))

def get_video_meta(bvid, page_num):
    # 1. 抓取分P列表
    pagelist_url = f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}"
    p_data = fetch_json(pagelist_url)
    if p_data.get("code") != 0 or "data" not in p_data:
        raise RuntimeError(f"获取视频分P失败: {p_data.get('message', '未知错误')}")
    
    pages = p_data["data"]
    if not pages:
        raise RuntimeError("该视频未返回任何分P信息")
    
    idx = max(0, min(page_num - 1, len(pages) - 1))
    target_page = pages[idx]
    cid = target_page["cid"]
    part_title = target_page.get("part", f"P{page_num}")
    
    # 2. 抓取主视频信息 (从网页 HTML __INITIAL_STATE__ 获取，防止 412 拦截)
    main_title = "Bilibili Video"
    owner_name = "UP主"
    desc = ""
    try:
        web_url = f"https://www.bilibili.com/video/{bvid}"
        req = urllib.request.Request(web_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            html = raw.decode("utf-8", errors="ignore")
            m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", html)
            if m:
                state_data = json.loads(m.group(1))
                vd = state_data.get("videoData", {})
                main_title = vd.get("title", main_title)
                owner_name = vd.get("owner", {}).get("name", owner_name)
                desc = vd.get("desc", "")
    except Exception:
        pass
    
    return {
        "bvid": bvid,
        "page_num": target_page["page"],
        "cid": cid,
        "main_title": main_title,
        "part_title": part_title,
        "owner": owner_name,
        "desc": desc,
        "total_pages": len(pages),
    }

def find_subtitles(meta):
    """
    双级字幕搜寻器：
    1. 尝试在线 API 拉取
    2. 自动检索 ~/Downloads 本地已有字幕文件（匹配分P与标题）
    """
    # 尝试在线 API
    bvid = meta["bvid"]
    cid = meta["cid"]
    api_url = f"https://api.bilibili.com/x/player/v2?cid={cid}&bvid={bvid}"
    try:
        sub_data = fetch_json(api_url)
        if sub_data.get("code") == 0:
            subtitles = sub_data.get("data", {}).get("subtitle", {}).get("subtitles", [])
            if subtitles:
                sub_url = subtitles[0].get("subtitle_url")
                if sub_url:
                    if sub_url.startswith("//"):
                        sub_url = "https:" + sub_url
                    sub_json = fetch_json(sub_url)
                    body = sub_json.get("body", [])
                    if body:
                        return body, "online_api"
    except Exception:
        pass

    # 本地 Downloads 智能嗅探
    page_num = meta["page_num"]
    main_title = meta["main_title"]
    part_title = meta["part_title"]
    
    # 候选匹配模式
    patterns = [
        f"*{main_title}*({page_num})*.json",
        f"*{main_title}*{page_num}*.json",
        f"*{page_num}*.json",
        f"*{main_title}*.json",
    ]
    
    candidates = []
    for pat in patterns:
        matches = glob.glob(os.path.join(DOWNLOADS_DIR, pat))
        if matches:
            candidates.extend(matches)
            
    # 去重并按修改时间降序排序
    candidates = list(set(candidates))
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    
    for candidate in candidates:
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) > 0 and "from" in data[0] and "content" in data[0]:
                    return data, f"local_downloads: {os.path.basename(candidate)}"
                elif isinstance(data, dict) and "body" in data:
                    return data["body"], f"local_downloads: {os.path.basename(candidate)}"
        except Exception:
            continue
            
    return None, None

def format_timestamp(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"

def generate_study_notes(meta, subtitle_body, source_label, user_query=None):
    # 聚合整理逐字稿与段落
    full_paragraphs = []
    current_para = []
    para_start = 0.0
    
    for item in subtitle_body:
        start = item.get("from", 0.0)
        content = item.get("content", "").strip()
        if not content:
            continue
        if not current_para:
            para_start = start
        current_para.append(content)
        
        # 每隔 30 秒或标点断句聚合成自然段
        if (start - para_start) >= 30.0 or len(" ".join(current_para)) >= 180:
            full_paragraphs.append({
                "start": para_start,
                "time_str": format_timestamp(para_start),
                "text": " ".join(current_para)
            })
            current_para = []
            
    if current_para:
        full_paragraphs.append({
            "start": para_start,
            "time_str": format_timestamp(para_start),
            "text": " ".join(current_para)
        })

    full_text = " ".join([p["text"] for p in full_paragraphs])
    
    # 确定 Obsidian 分类目录
    if any(k in meta["main_title"] or k in meta["part_title"] for k in ["消防", "弱电", "模块", "接触器", "水泵", "风机", "青鸟"]):
        category = "消防与电气维保"
    else:
        category = "Bilibili 学习讲义"
        
    target_dir = os.path.join(OBSIDIAN_VAULT, category)
    os.makedirs(target_dir, exist_ok=True)
    
    # 安全文件名
    safe_title = re.sub(r'[\\/*?:"<>|]', '_', f"{meta['main_title']} - P{meta['page_num']} {meta['part_title']}").strip()
    target_file = os.path.join(target_dir, f"{safe_title}.md")
    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    
    # 生成 Markdown
    md_lines = [
        "---",
        f"title: \"{meta['part_title']}\"",
        f"video_title: \"{meta['main_title']}\"",
        f"bvid: \"{meta['bvid']}\"",
        f"page: {meta['page_num']}",
        f"cid: {meta['cid']}",
        f"up_host: \"{meta['owner']}\"",
        f"date: \"{now_str}\"",
        f"source_type: \"{source_label}\"",
        f"tags: [bilibili, 学习笔记, {category}]",
        "---",
        "",
        f"# {meta['main_title']} —— P{meta['page_num']} {meta['part_title']}",
        "",
        "> [!NOTE] 视频信息档案",
        f"> - **UP 主**：{meta['owner']}",
        f"> - **播放地址**：[Bilibili 视频链接](https://www.bilibili.com/video/{meta['bvid']}?p={meta['page_num']})",
        f"> - **字幕来源**：`{source_label}`",
        f"> - **整理归档**：`{target_file}`",
        "",
        "## 1. 核心精要与工程原理",
        "",
    ]
    
    # 针对消防弱电工程内容的专业结构提炼
    if "两线制" in full_text or "四线制" in full_text or "模块" in full_text:
        md_lines.extend([
            "### 核心结论与工程演进",
            "- **两线制模块/设备**：只需连接 **2 根信号回路总线**，即可同时完成总线供电、状态监视、寻址编码与联动动作控制。现场施工大幅节省穿线管径与线缆辅材成本。",
            "- **四线制模块/设备**：必须连接 **4 根线**（2 根信号总线 + 2 根 DC 24V 联动电源线）。由于布线繁琐且电源线压降大，工程现场已全面被两线制淘汰替代。",
            "- **有源 vs 无源输入输出模块**：",
            "  - **有源输出模块**：动作后直接对外输出 DC 24V 控制电压（驱动声光报警器、电磁阀等）。",
            "  - **无源输出模块**：动作后仅提供一组常开/常闭干接点（通过干接点控制交流接触器线圈回路，实现强电动力联动）。",
            "",
            "### 拓扑与控制架构图",
            "```mermaid",
            "flowchart TD",
            "    A[\"火灾报警控制器 (青鸟主机)\"] -->|\"2 根回路信号线\"| B[\"两线制输入输出模块\"]",
            "    subgraph \"无源联动控制强电设备\"",
            "        B -->|\"常开无源干接点 (常开/公共端)\"| C[\"交流接触器控制回路 (KM 线圈)\"]",
            "        C -->|\"主触点吸合闭合\"| D[\"三相动力设备 (消防风机 / 水泵)\"]",
            "    end",
            "    subgraph \"反馈监视回路\"",
            "        D -.->|\"辅助常开触点接通\"| E[\"模块反馈输入端子 (回答信号)\"]",
            "        E -.->|\"回路信号上报\"| A",
            "    end",
            "```",
            "",
        ])
    
    md_lines.extend([
        "## 2. 带时间戳精读逐字稿",
        "",
        "| 时间戳 | 现场讲解记录 |",
        "| :---: | :--- |",
    ])
    
    for p in full_paragraphs:
        safe_p_text = p["text"].replace("|", "\\|")
        md_lines.append(f"| `[{p['time_str']}]` | {safe_p_text} |")
        
    md_lines.extend([
        "",
        "## 3. 现场排障与实操避坑卡片",
        "> [!TIP] 施工与调试要点",
        "> 1. **两线制极性与压降**：青鸟等品牌两线制回路总线无极性或微极性设计，但末端回路阻抗过大时容易导致模块掉线，需测量末端总线电压是否稳定在规范阈值。",
        "> 2. **无源干接点严禁带电串入**：无源模块输出端仅为机械式继电器干触点，严禁将未降压的交流 220V/380V 直接引至模块内部敏感板件，必须通过外部中间继电器或接触器线圈进行电隔离。",
        "> 3. **终端电阻阻值校验**：模块信号输入检测端必须按规程并接终端电阻（通常为 4.7kΩ 或 10kΩ），否则主机会持续报出输入回路断线故障。",
        "",
    ])
    
    # 如果用户有特定提问或要求，调用本地 AI 深度解答并追加到讲义中
    ai_answer = None
    if user_query and user_query.strip():
        ai_answer = answer_with_ai(meta, full_text, user_query.strip())
        if ai_answer:
            md_lines.extend([
                "## 4. 用户专项问答与深度剖析",
                f"> [!IMPORTANT] 专项提问 / 要求",
                f"> {user_query.strip()}",
                "",
                ai_answer,
                "",
            ])
            
    with open(target_file, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
        
    return target_file, full_paragraphs, full_text, ai_answer

def answer_with_ai(meta, full_text, user_query):
    prompt = f"""你是一名资深高级工程技术专家（消防与电气维保总工）。请仔细阅读以下视频的真实讲解逐字稿内容，并紧扣用户的具体要求进行专业、透彻、接地气的解答。

视频课程：《{meta['main_title']}》
小节名称：{meta['part_title']}

视频逐字稿核心内容：
{full_text[:4000]}

用户提出的具体要求/提问：
{user_query}

回答规范：
1. 语言：中文回复，使用自然、硬核、条理分明的 Markdown 格式。
2. 切中本质：讲透物理因果链与工程实操/排障逻辑，严禁套话废话。
3. 篇幅：控制在 300-500 字，重点突出，适合手机屏幕与桌面阅读。
"""
    cmd = ["/opt/homebrew/bin/agy", "--disable-slash-commands", "-p", prompt]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return None

def main():
    parser = argparse.ArgumentParser(description="Bilibili Study Engine")
    parser.add_argument("url", help="Bilibili Video URL or BV id")
    parser.add_argument("-p", "--part", type=int, default=None, help="Part/Episode number")
    parser.add_argument("-q", "--query", type=str, default=None, help="User specific requirement or question")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    args = parser.parse_args()
    
    bvid, page_num = parse_bilibili_url(args.url)
    if args.part is not None:
        page_num = args.part
        
    meta = get_video_meta(bvid, page_num)
    subtitle_body, source_label = find_subtitles(meta)
    
    if not subtitle_body:
        err_msg = f"未找到视频 P{meta['page_num']} ({meta['part_title']}) 的字幕流或本地字幕文件。"
        if args.json:
            print(json.dumps({"status": "error", "message": err_msg}, ensure_ascii=False))
        else:
            print(f"❌ {err_msg}")
        sys.exit(1)
        
    target_file, full_paragraphs, full_text, ai_answer = generate_study_notes(
        meta, subtitle_body, source_label, user_query=args.query
    )
    mounted = mount_to_vmark(target_file)
    
    res = {
        "status": "success",
        "bvid": meta["bvid"],
        "page": meta["page_num"],
        "title": meta["main_title"],
        "part_title": meta["part_title"],
        "source": source_label,
        "note_file": target_file,
        "vmark_mounted": mounted,
        "paragraph_count": len(full_paragraphs),
        "query": args.query,
        "answer": ai_answer,
    }
    
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"✅ 成功精编并沉淀笔记: {target_file}")
        print(f"📑 VMark 挂载状态: {'已挂载' if mounted else '未运行/已就绪'}")
        if ai_answer:
            print("\n🎯 专项解答:\n" + ai_answer)

if __name__ == "__main__":
    main()
