#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PTT 運彩版 - 單次檢查（GitHub Actions 用，不含常駐迴圈）"""

import sys
import ptt_monitor as M


def main():
    seen, seen_comments = M.load_state()
    art_titles: dict[str, str] = {}

    is_init = not seen

    try:
        articles = M.fetch_articles()
    except Exception as e:
        M._log(f"[取得文章失敗] {e}")
        sys.exit(1)

    current_ids = {a["id"] for a in articles}
    for a in articles:
        art_titles[a["id"]] = a["title"]

    if is_init:
        for a in articles:
            seen.add(a["id"])
        M.save_state(seen, seen_comments, keep_ids=current_ids)
        M._log(f"[初始化] 記錄 {len(articles)} 篇現有文章，下次執行起有新文章才通知")
        return

    new = [a for a in articles if a["id"] not in seen]

    if new:
        M._log(f"發現 {len(new)} 篇新文章！")
        for article in new:
            watched = article["author"].lower() in M.WATCH_AUTHORS
            tag = "★ " if watched else ""
            M._log(f"  + {tag}{article['title']}  ({article['author']})")
            ok = M.send_telegram(M.make_article_message(article))
            M._log(f"    TG: {'OK' if ok else 'SKIP'}")
            seen.add(article["id"])
        M.save_state(seen, seen_comments, keep_ids=current_ids)
    else:
        M._log(f"無新文章（共 {len(articles)} 篇）")
        M.save_state(seen, seen_comments, keep_ids=current_ids)

    if M.WATCH_AUTHORS:
        M._log(f"[留言掃描] 掃描近 {M.COMMENT_WATCH_HOURS}h 內文章...")
        n = M.check_comments(seen, seen_comments, art_titles)
        if n > 0:
            M.save_state(seen, seen_comments, keep_ids=current_ids)
        M._log(f"[留言掃描] 完成，新通知 {n} 則")


if __name__ == "__main__":
    main()
