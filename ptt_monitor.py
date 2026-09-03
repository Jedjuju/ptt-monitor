#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PTT 運彩版 (SportLottery) 新文章監控
常駐偵測，發現新文章即透過 Telegram Bot 發送通知

設定方式（任選其一）：
  1. 環境變數：set TELEGRAM_TOKEN=xxx  &  set TELEGRAM_CHAT_ID=xxx
  2. 同目錄 ptt_config.json：{"token": "xxx", "chat_id": "xxx"}

可選設定（ptt_config.json）：
  watch_authors       : 監控特定作者留言 / 發文，例如 ["abc", "def"]
  expire_days         : 已知文章 ID 保留天數（預設 3）
  comment_watch_hours : 掃描留言的文章時間範圍（幾小時內的文章，預設 24）
  comment_poll_sec    : 留言掃描間隔秒數（預設 300）
"""

import urllib.request
import urllib.error
import json
import re
import ssl
import time
import datetime
import os
import sys

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ── 設定 ─────────────────────────────────────────────────────────────────────

PTT_BOARD = "SportLottery"
PTT_INDEX = f"https://www.ptt.cc/bbs/{PTT_BOARD}/index.html"
PTT_BASE  = "https://www.ptt.cc"

POLL_SEC  = 60

_HERE       = os.path.dirname(os.path.abspath(__file__))
STATE_FILE  = os.path.join(_HERE, "ptt_state.json")
CONFIG_FILE = os.path.join(_HERE, "ptt_config.json")


def _load_config() -> tuple[str, str, dict]:
    """讀取 Telegram Token / Chat ID 及其他選用設定，環境變數優先"""
    token   = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    cfg: dict = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    if not token:
        token = cfg.get("token", "")
    if not chat_id:
        chat_id = cfg.get("chat_id", "")
    return token, chat_id, cfg


TG_TOKEN, TG_CHAT_ID, _CFG = _load_config()

EXPIRE_DAYS         = int(_CFG.get("expire_days", 3))
WATCH_AUTHORS       = [a.lower() for a in _CFG.get("watch_authors", [])]
COMMENT_WATCH_HOURS = int(_CFG.get("comment_watch_hours", 24))
COMMENT_POLL_SEC    = int(_CFG.get("comment_poll_sec", 300))


# ── PTT 爬取 ──────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":         "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":         "identity",
    "Cookie":                  "over18=1",
    "Connection":              "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_RE_ARTICLE = re.compile(
    r'<div class="(?:r-pin )?r-ent">'
    r'.*?<div class="nrec">(.*?)</div>'
    r'.*?<div class="title">(.*?)</div>'
    r'.*?<div class="author">(.*?)</div>'
    r'.*?<div class="date">\s*(.*?)\s*</div>',
    re.DOTALL,
)
_RE_LINK  = re.compile(r'href="(/bbs/[^"]+\.html)"')
_RE_TITLE = re.compile(r'<a[^>]*>([^<]+)</a>')
_RE_NREC  = re.compile(r'<span[^>]*>([^<]+)</span>')
_RE_TAGS  = re.compile(r'<[^>]+>')

_RE_PUSH = re.compile(
    r'<div class="push">'
    r'.*?<span[^>]*push-tag[^>]*>(.*?)</span>'
    r'.*?<span[^>]*push-userid[^>]*>(.*?)</span>'
    r'.*?<span[^>]*push-content[^>]*>(.*?)</span>'
    r'.*?<span[^>]*push-ipdatetime[^>]*>(.*?)</span>',
    re.DOTALL,
)


def _strip(s: str) -> str:
    return _RE_TAGS.sub("", s).strip()


def _id_to_ts(art_id: str) -> int:
    """從 PTT 文章 ID（M.{unix_ts}.A.xxx）解出 Unix 時間戳，失敗傳回 0"""
    m = re.match(r'M\.(\d+)\.', art_id)
    return int(m.group(1)) if m else 0


def fetch_articles() -> list[dict]:
    req = urllib.request.Request(PTT_INDEX, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    articles: list[dict] = []
    for m in _RE_ARTICLE.finditer(html):
        nrec_raw, title_block, author, date = (
            m.group(1), m.group(2), m.group(3).strip(), m.group(4).strip()
        )
        link_m = _RE_LINK.search(title_block)
        if not link_m:
            continue

        href   = link_m.group(1)
        art_id = href.rsplit("/", 1)[-1].replace(".html", "")
        title  = _strip(_RE_TITLE.search(title_block).group(0) if _RE_TITLE.search(title_block) else "")
        nrec   = _strip(_RE_NREC.search(nrec_raw).group(0) if _RE_NREC.search(nrec_raw) else "")

        articles.append({
            "id":     art_id,
            "title":  title,
            "url":    PTT_BASE + href,
            "author": author,
            "date":   date,
            "nrec":   nrec or "0",
        })

    return articles


def fetch_comments(url: str) -> list[dict]:
    """從 PTT 文章頁面抓取推文 / 留言列表"""
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    comments: list[dict] = []
    for m in _RE_PUSH.finditer(html):
        tag, userid, content, dt_str = (
            m.group(1).strip(),
            m.group(2).strip(),
            m.group(3).strip().lstrip(": "),
            m.group(4).strip(),
        )
        comments.append({"tag": tag, "userid": userid, "content": content, "dt": dt_str})
    return comments


# ── 狀態儲存 ──────────────────────────────────────────────────────────────────

def load_state() -> tuple[set[str], dict[str, list[str]]]:
    """載入已知文章 ID 集合與已通知留言記錄"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return (
                set(data.get("seen_ids", [])),
                data.get("seen_comments", {}),
            )
        except Exception:
            pass
    return set(), {}


def save_state(
    seen: set[str],
    seen_comments: dict[str, list[str]],
    keep_ids: set[str] = frozenset(),
) -> tuple[set[str], dict[str, list[str]]]:
    """過期並儲存狀態，傳回已修剪後的集合。
    keep_ids 中的 ID（如當前頁面的置頂文）即使時間戳過舊也不過期。"""
    cutoff = time.time() - EXPIRE_DAYS * 86400
    pruned = {aid for aid in seen if _id_to_ts(aid) >= cutoff or aid in keep_ids}
    pruned_comments = {
        aid: v for aid, v in seen_comments.items()
        if _id_to_ts(aid) >= cutoff or aid in keep_ids
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "seen_ids":      sorted(pruned),
            "seen_comments": pruned_comments,
            "updated":       datetime.datetime.now().isoformat(),
        }, f, ensure_ascii=False)
    return pruned, pruned_comments


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        return False

    url     = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id":                  TG_CHAT_ID,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        _log(f"[TG ERROR] {e}")
        return False


def _nrec_icon(nrec: str) -> str:
    if nrec == "爆":
        return "[爆]"
    if nrec.startswith("X"):
        return f"[{nrec}]"
    try:
        n = int(nrec)
        if n >= 10:
            return f"[{n}]"
    except ValueError:
        pass
    return f"[{nrec}]" if nrec else ""


def make_article_message(article: dict) -> str:
    nrec_str = _nrec_icon(article["nrec"])
    watched  = article["author"].lower() in WATCH_AUTHORS
    header   = "PTT 運彩版  ★ 關注作者發文" if watched else "PTT 運彩版  新文章通知"
    return (
        f"<b>{header}</b>\n\n"
        f"<b>{article['title']}</b>\n\n"
        f"作者：{article['author']}\n"
        f"日期：{article['date']}\n"
        f"推文：{nrec_str or article['nrec']}\n\n"
        f'<a href="{article["url"]}">前往閱讀</a>'
    )


def make_comment_message(art_id: str, art_title: str, comment: dict) -> str:
    url       = f"{PTT_BASE}/bbs/{PTT_BOARD}/{art_id}.html"
    tag_label = {"推": "推文", "噓": "噓文", "→": "回文"}.get(comment["tag"], "留言")
    return (
        f"<b>PTT 運彩版  ★ 關注作者留言</b>\n\n"
        f"作者：<b>{comment['userid']}</b>  {tag_label}\n"
        f"內容：{comment['content']}\n"
        f"時間：{comment['dt']}\n\n"
        f"文章：{art_title}\n"
        f'<a href="{url}">前往閱讀</a>'
    )


# ── 主程式 ────────────────────────────────────────────────────────────────────

def _log(msg: str):
    ts   = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        sys.stdout.buffer.write(line.encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        print(line, end="", errors="replace")


def check_comments(
    seen: set[str],
    seen_comments: dict[str, list[str]],
    art_titles: dict[str, str],
) -> int:
    """掃描近期文章留言，對監控作者發 TG 通知。傳回新通知數量。"""
    if not WATCH_AUTHORS:
        return 0

    cutoff  = time.time() - COMMENT_WATCH_HOURS * 3600
    recent  = [aid for aid in seen if _id_to_ts(aid) >= cutoff]
    notified = 0

    for art_id in recent:
        url = f"{PTT_BASE}/bbs/{PTT_BOARD}/{art_id}.html"
        try:
            comments = fetch_comments(url)
        except Exception as e:
            _log(f"  [留言讀取失敗] {art_id}: {e}")
            time.sleep(1)
            continue

        already = set(seen_comments.get(art_id, []))
        for c in comments:
            if c["userid"].lower() not in WATCH_AUTHORS:
                continue
            key = f"{c['userid']}_{c['dt']}"
            if key in already:
                continue
            title = art_titles.get(art_id, art_id)
            ok    = send_telegram(make_comment_message(art_id, title, c))
            _log(f"  [留言通知] {c['userid']} 在 {art_id}  TG: {'OK' if ok else 'SKIP'}")
            already.add(key)
            notified += 1
        seen_comments[art_id] = list(already)
        time.sleep(1)   # 對 PTT 伺服器禮貌延遲

    return notified


def main():
    _log(f"PTT 運彩版監控 啟動  ({PTT_INDEX})")
    _log(f"輪詢間隔：{POLL_SEC}s  |  狀態檔：{STATE_FILE}")
    _log(f"過期天數：{EXPIRE_DAYS}d  |  留言監控：{COMMENT_WATCH_HOURS}h 內文章  掃描間隔：{COMMENT_POLL_SEC}s")
    if WATCH_AUTHORS:
        _log(f"監控作者：{', '.join(WATCH_AUTHORS)}")
    else:
        _log("未設定監控作者（僅監控新文章）")

    if not TG_TOKEN or not TG_CHAT_ID:
        _log("[警告] 未設定 Telegram，執行但不發送通知")
        _log("  請建立 ptt_config.json 或設定環境變數")
        _log('  ptt_config.json 格式：{"token": "BOT_TOKEN", "chat_id": "CHAT_ID"}')
    else:
        _log(f"[TG] Bot token 已載入，Chat ID：{TG_CHAT_ID}")

    seen, seen_comments = load_state()
    art_titles: dict[str, str] = {}
    _log(f"已知文章：{len(seen)} 篇")

    if not seen:
        try:
            articles = fetch_articles()
            init_ids = {a["id"] for a in articles}
            for a in articles:
                seen.add(a["id"])
                art_titles[a["id"]] = a["title"]
            seen, seen_comments = save_state(seen, seen_comments, keep_ids=init_ids)
            _log(f"[初始化] 記錄 {len(articles)} 篇現有文章，下次執行起有新文章才通知")
        except Exception as e:
            _log(f"[初始化失敗] {e}")
        _log("等待第一輪輪詢...")
        time.sleep(POLL_SEC)

    last_comment_check = 0.0
    current_ids: set[str] = set()  # 當前頁面可見的所有文章 ID（含置頂）

    while True:
        try:
            articles    = fetch_articles()
            current_ids = {a["id"] for a in articles}
            new         = [a for a in articles if a["id"] not in seen]

            for a in articles:
                art_titles[a["id"]] = a["title"]

            if new:
                _log(f"發現 {len(new)} 篇新文章！")
                for article in new:
                    watched = article["author"].lower() in WATCH_AUTHORS
                    tag     = "★ " if watched else ""
                    _log(f"  + {tag}{article['title']}  ({article['author']})")
                    ok = send_telegram(make_article_message(article))
                    _log(f"    TG: {'OK' if ok else 'SKIP (未設定或失敗)'}")
                    seen.add(article["id"])
                seen, seen_comments = save_state(seen, seen_comments, keep_ids=current_ids)
                art_titles = {k: v for k, v in art_titles.items() if k in seen}
            else:
                _log(f"無新文章（共 {len(articles)} 篇）")

            # 定期留言掃描
            if WATCH_AUTHORS and (time.time() - last_comment_check >= COMMENT_POLL_SEC):
                _log(f"[留言掃描] 掃描近 {COMMENT_WATCH_HOURS}h 內文章留言中...")
                n = check_comments(seen, seen_comments, art_titles)
                seen, seen_comments = save_state(seen, seen_comments, keep_ids=current_ids)
                art_titles = {k: v for k, v in art_titles.items() if k in seen}
                _log(f"[留言掃描] 完成，新通知 {n} 則")
                last_comment_check = time.time()

        except urllib.error.URLError as e:
            _log(f"[網路錯誤] {e}  (將在 {POLL_SEC}s 後重試)")
        except KeyboardInterrupt:
            _log("收到中斷信號，停止監控")
            break
        except Exception as e:
            _log(f"[未知錯誤] {e}")

        try:
            time.sleep(POLL_SEC)
        except KeyboardInterrupt:
            _log("收到中斷信號，停止監控")
            break


if __name__ == "__main__":
    main()
