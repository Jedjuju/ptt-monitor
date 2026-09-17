#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台灣運彩爬蟲 - 自評勝率分析
拉取足球、棒球、網球、籃球及電競的場中、今日與早盤賽事，計算自評勝率並輸出日報
"""

import urllib.request
import urllib.error
import json
import datetime
import os
import sys

BASE_URL = "https://blob3rd.sportslottery.com.tw/apidata"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, */*",
    "Referer": "https://www.sportslottery.com.tw/",
    "Origin": "https://www.sportslottery.com.tw",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# 目標球種：(sport_id, abbreviation, 中文名稱)
SPORTS = [
    ("34740.1", "FBL", "足球"),
    ("34765.1", "BKB", "籃球"),
    ("34731.1", "BSB", "棒球"),
    ("34758.1", "TNS", "網球"),
    ("34746.1", "LOL", "電競"),
]

SPORT_BY_ID = {s[0]: s for s in SPORTS}
TARGET_SPORT_IDS = {s[0] for s in SPORTS}

# 台灣時區
TW_TZ = datetime.timezone(datetime.timedelta(hours=8))


# ─── 資料取得 ────────────────────────────────────────────────────────────────

def fetch_json(url: str) -> list | dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_live_games() -> list[dict]:
    """取得場中賽事（過濾目標球種）"""
    try:
        games = fetch_json(f"{BASE_URL}/Live/Games.zh.json")
        return [g for g in games if g.get("si") in TARGET_SPORT_IDS]
    except Exception as e:
        print(f"  [警告] 場中賽事取得失敗：{e}", file=sys.stderr)
        return []


def get_pre_games(sport_id: str) -> list[dict]:
    """取得某球種的盤前賽事（今日 + 早盤）"""
    try:
        return fetch_json(f"{BASE_URL}/Pre/{sport_id}-Games.zh.json")
    except Exception as e:
        print(f"  [警告] 盤前賽事取得失敗（{sport_id}）：{e}", file=sys.stderr)
        return []


# ─── 賠率計算 ────────────────────────────────────────────────────────────────

def find_moneyline_market(game: dict) -> dict | None:
    """找出主要勝負盤（不讓分，iht=False 且 mv=None 的第一個含 A/H 選項的盤口）"""
    for m in game.get("ms", []):
        if m.get("iht") or m.get("mv") is not None:
            continue
        v_set = {c.get("v") for c in m.get("cs", [])}
        if "A" in v_set and "H" in v_set:
            return m
    return None


def calc_win_rates(market: dict) -> dict[str, dict]:
    """
    計算各選項自評勝率（歸一化隱含機率）。

    pd = price denominator（分母）
    pu = price numerator（分子）
    Fractional odds = pu/pd  →  Decimal odds = (pd+pu)/pd
    Implied prob = pd / (pd+pu)
    Self-assessed win rate = implied_prob / Σ implied_probs  （移除書商抽成）
    """
    raw: dict[str, dict] = {}

    for c in market.get("cs", []):
        v = c.get("v")
        if not v:
            continue
        try:
            pd = float(c["pd"])
            pu = float(c["pu"])
        except (KeyError, TypeError, ValueError):
            continue
        if pd <= 0 or pu <= 0:
            continue

        decimal_odds = (pd + pu) / pd
        implied = pd / (pd + pu)
        raw[v] = {
            "name": c.get("name") or c.get("sn") or v,
            "decimal_odds": decimal_odds,
            "implied_prob": implied,
        }

    if not raw:
        return {}

    overround = sum(r["implied_prob"] for r in raw.values())
    for info in raw.values():
        info["win_rate"] = info["implied_prob"] / overround if overround else 0
        info["overround"] = overround

    return raw


# ─── 分類 ───────────────────────────────────────────────────────────────────

def categorize(
    live_games: list[dict],
    pre_by_sport: dict[str, list[dict]],
    today_str: str,
) -> dict[str, list[dict]]:
    """將賽事分類為 場中 / 今日 / 早盤"""
    result: dict[str, list[dict]] = {"場中": [], "今日": [], "早盤": []}

    for g in live_games:
        sid = g.get("si", "")
        g["_sport_name"] = SPORT_BY_ID.get(sid, ("", "", "其他"))[2]
        result["場中"].append(g)

    for sid, games in pre_by_sport.items():
        sport_name = SPORT_BY_ID.get(sid, ("", "", "其他"))[2]
        for g in games:
            g["_sport_name"] = sport_name
            kt = g.get("kt", "")
            if kt.startswith(today_str):
                result["今日"].append(g)
            else:
                result["早盤"].append(g)

    return result


# ─── Markdown 產生 ──────────────────────────────────────────────────────────

def fmt_dt(dt_str: str) -> str:
    """轉換 ISO datetime 為台灣時區的可讀格式"""
    try:
        dt = datetime.datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TW_TZ)
        return dt.astimezone(TW_TZ).strftime("%m/%d %H:%M")
    except Exception:
        return dt_str


def pct(value: float) -> str:
    return f"{value:.1%}"


def decimal_fmt(value: float) -> str:
    return f"{value:.2f}"


CHOICE_LABEL = {"H": "主場", "D": "平局", "A": "客場"}
CHOICE_ORDER = ["H", "D", "A"]


def match_win_rate_summary(rates: dict[str, dict]) -> str:
    """
    本場賽事層級的自評勝率摘要（單行，使用勝負盤）。
    最高勝率方以粗體標示並附上「（最高）」。
    """
    if not rates:
        return "(無資料)"

    best_v = max(rates, key=lambda v: rates[v]["win_rate"])
    parts: list[str] = []

    for v in CHOICE_ORDER:
        if v not in rates:
            continue
        info = rates[v]
        label = CHOICE_LABEL.get(v, v)
        wr = pct(info["win_rate"])
        if v == best_v:
            parts.append(f"**{label} {info['name']} {wr}（最高）**")
        else:
            parts.append(f"{label} {info['name']} {wr}")

    return " ╱ ".join(parts)


def render_market(market: dict) -> list[str]:
    """
    將單一玩法（market）轉為 Markdown 表格。
    適用於所有玩法，不論 choice 是否有 v（H/D/A）欄位。
    """
    mname = market.get("name", "（未命名）")
    choices = [c for c in market.get("cs", []) if c.get("pd") and c.get("pu")]
    if not choices:
        return []

    items: list[dict] = []
    for c in choices:
        try:
            pd_val = float(c["pd"])
            pu_val = float(c["pu"])
        except (TypeError, ValueError):
            continue
        if pd_val <= 0 or pu_val <= 0:
            continue
        label = c.get("name") or c.get("sn") or c.get("v") or "?"
        decimal_odds = (pd_val + pu_val) / pd_val
        implied = pd_val / (pd_val + pu_val)
        items.append({"label": label, "decimal_odds": decimal_odds, "implied_prob": implied})

    if not items:
        return []

    total_implied = sum(x["implied_prob"] for x in items)
    for item in items:
        item["win_rate"] = item["implied_prob"] / total_implied if total_implied else 0

    overround_str = pct(total_implied - 1)
    best_idx = max(range(len(items)), key=lambda i: items[i]["win_rate"])

    lines = [
        f"**{mname}**（書商抽成 {overround_str}）",
        "",
        "| 選項 | 賠率 | 隱含機率 | 自評勝率 |",
        "|:-----|:----:|:--------:|:--------:|",
    ]
    for i, item in enumerate(items):
        wr = f"**{pct(item['win_rate'])}**" if i == best_idx else pct(item["win_rate"])
        lines.append(
            f"| {item['label']} "
            f"| {decimal_fmt(item['decimal_odds'])} "
            f"| {pct(item['implied_prob'])} "
            f"| {wr} |"
        )

    return lines


def render_game(g: dict, category: str) -> str:
    sport = g.get("_sport_name", "")
    away = g.get("an", "")
    home = g.get("hn", "")
    kt = fmt_dt(g.get("kt", ""))
    tn = g.get("tn", "")

    lines = [
        f"#### {sport}｜{away} vs {home}",
        f"- **賽事**：{tn}",
        f"- **開賽**：{kt}　**類別**：{category}",
    ]

    # 本場自評勝率摘要（使用勝負盤）
    moneyline = find_moneyline_market(g)
    if moneyline:
        rates = calc_win_rates(moneyline)
        if rates:
            lines.append(f"- **本場自評勝率**：{match_win_rate_summary(rates)}")

    markets = g.get("ms", [])
    if not markets:
        lines += ["", "- (!) 無盤口資料", ""]
        return "\n".join(lines)

    lines.append("")

    # 每個玩法的自評勝率
    for m in markets:
        market_lines = render_market(m)
        if market_lines:
            lines.extend(market_lines)
            lines.append("")

    return "\n".join(lines)


def build_markdown(categorized: dict[str, list[dict]], today_str: str) -> str:
    now = datetime.datetime.now(TW_TZ)
    parts: list[str] = []

    # ── 標題 ──
    parts.append(f"# 台灣運彩自評勝率日報 — {today_str}")
    parts.append("")
    parts.append(f"> 更新時間：{now.strftime('%Y-%m-%d %H:%M:%S')} (台灣時間)")
    parts.append("")

    # ── 總覽 ──
    total_live = len(categorized["場中"])
    total_today = len(categorized["今日"])
    total_early = len(categorized["早盤"])
    total = total_live + total_today + total_early

    parts += [
        "## 總覽",
        "",
        "| 類別 | 場數 |",
        "|:----:|:----:|",
        f"| 場中 | {total_live} |",
        f"| 今日 | {total_today} |",
        f"| 早盤 | {total_early} |",
        f"| **合計** | **{total}** |",
        "",
    ]

    # ── 球種分布 ──
    sport_counts: dict[str, dict[str, int]] = {}
    for cat, games in categorized.items():
        for g in games:
            sn = g.get("_sport_name", "其他")
            sport_counts.setdefault(sn, {"場中": 0, "今日": 0, "早盤": 0})
            sport_counts[sn][cat] += 1

    if sport_counts:
        parts += [
            "### 球種分布",
            "",
            "| 球種 | 場中 | 今日 | 早盤 | 合計 |",
            "|:----:|:----:|:----:|:----:|:----:|",
        ]
        for sport, counts in sport_counts.items():
            t = sum(counts.values())
            parts.append(
                f"| {sport} | {counts['場中']} | {counts['今日']} | {counts['早盤']} | {t} |"
            )
        parts.append("")

    # ── 賽事明細 ──
    for category in ["場中", "今日", "早盤"]:
        games = categorized[category]
        if not games:
            continue

        parts.append(f"---")
        parts.append(f"")
        parts.append(f"## {category}賽事（{len(games)} 場）")
        parts.append("")

        # 按球種分組
        by_sport: dict[str, list[dict]] = {}
        for g in games:
            sn = g.get("_sport_name", "其他")
            by_sport.setdefault(sn, []).append(g)

        # 按開賽時間排序（每球種內）
        for sn, sg in by_sport.items():
            sg.sort(key=lambda x: x.get("kt", ""))

        for sport_name, sport_games in by_sport.items():
            parts.append(f"### {sport_name}（{len(sport_games)} 場）")
            parts.append("")
            for g in sport_games:
                parts.append(render_game(g, category))

    return "\n".join(parts)


# ─── 主程式 ─────────────────────────────────────────────────────────────────

def main():
    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")

    print(f"[台灣運彩] 自評勝率分析 {today_str}")
    print("=" * 50)

    print("[1/3] 取得場中賽事...")
    live_games = get_live_games()
    print(f"      場中：{len(live_games)} 場")

    print("[2/3] 取得盤前賽事...")
    pre_by_sport: dict[str, list[dict]] = {}
    for sid, abb, name in SPORTS:
        games = get_pre_games(sid)
        pre_by_sport[sid] = games
        print(f"      {name}：{len(games)} 場")

    categorized = categorize(live_games, pre_by_sport, today_str)
    print(
        f"\n      分類：場中 {len(categorized['場中'])} /"
        f" 今日 {len(categorized['今日'])} /"
        f" 早盤 {len(categorized['早盤'])}"
    )

    print("[3/3] 生成報告...")
    md = build_markdown(categorized, today_str)

    out_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, f"{today_str}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)

    total = sum(len(v) for v in categorized.values())
    print(f"[完成] 報告已儲存：{out_path}")
    print(f"       共 {total} 場賽事")


if __name__ == "__main__":
    main()
