#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自動偵測 Telegram Bot 收到的群組/頻道 Chat ID
並寫入 ptt_config.json
"""
import json, urllib.request, sys, os

sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(_HERE, "ptt_config.json")

with open(CONFIG, encoding="utf-8") as f:
    cfg = json.load(f)

token = cfg.get("token", "")
if not token or "YOUR_BOT" in token:
    print("錯誤：請先填入 ptt_config.json 的 token")
    sys.exit(1)

url  = f"https://api.telegram.org/bot{token}/getUpdates"
data = json.loads(urllib.request.urlopen(url, timeout=10).read())
updates = data.get("result", [])

print(f"Bot 收到的所有對話（共 {len(updates)} 個 update）：\n")

found: list[dict] = []
seen = set()
for u in updates:
    for key in ["message", "channel_post", "my_chat_member", "chat_member", "callback_query"]:
        if key not in u:
            continue
        obj  = u[key]
        chat = obj.get("chat") or obj.get("message", {}).get("chat", {})
        if not chat or chat["id"] in seen:
            continue
        seen.add(chat["id"])
        entry = {
            "chat_id": chat["id"],
            "type":    chat["type"],
            "name":    chat.get("title") or chat.get("first_name") or "",
        }
        found.append(entry)
        tag = "[群組]" if chat["type"] in ("group", "supergroup") else \
              "[頻道]" if chat["type"] == "channel" else "[私訊]"
        print(f"  {tag} Chat ID: {chat['id']}  名稱: {entry['name']}")

print()

# 過濾出群組/頻道
candidates = [f for f in found if f["type"] in ("group", "supergroup", "channel")]

if not candidates:
    print("未偵測到任何群組或頻道。")
    print("請確認：")
    print("  1. 已將 bot 加入群組")
    print("  2. 已在群組裡傳一則訊息")
    print("  3. 然後重新執行此腳本")
    sys.exit(0)

if len(candidates) == 1:
    chosen = candidates[0]
    print(f"偵測到：{chosen['name']}  (ID: {chosen['chat_id']})")
else:
    print("偵測到多個群組/頻道，請選擇：")
    for i, c in enumerate(candidates):
        print(f"  {i+1}. [{c['type']}] {c['name']}  (ID: {c['chat_id']})")
    while True:
        try:
            choice = int(input("輸入編號：")) - 1
            if 0 <= choice < len(candidates):
                chosen = candidates[choice]
                break
        except (ValueError, KeyboardInterrupt):
            pass

# 寫入設定
cfg["chat_id"] = str(chosen["chat_id"])
with open(CONFIG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

print(f"\n已寫入 ptt_config.json：chat_id = {chosen['chat_id']}")
print("現在可以執行 python ptt_monitor.py 開始監控！")
