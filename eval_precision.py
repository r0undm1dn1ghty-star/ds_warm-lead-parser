#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живой замер точности Warm Lead Parser (замер 4).

Методика (как в замере 2, категория = ground truth):
  IT-услуги = категории Программирование / Сайты / Базы данных / Администрирование / AI / Мессенджеры.
  Контроль  = Дизайн, Тексты, Аудио-Видео, Реклама и Маркетинг, Инжиниринг, Переводы и пр.
  Пост считается «предсказанным лидом», если скор прошёл порог режима.

Режимы: наивный поиск по ключам, warm+ (>=30), hot (>=50), снайпер (>=S, перебор).
Контекст клиента: тот же прогон без правил v1.1 — вклад виден по дельте точности.

Запуск: python eval_precision.py            # ~2 минуты, сеть нужна
"""
import json
import re
import sys
import os
import html
import hashlib
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "integrations"))

import warm_lead_parser as wlp          # noqa: E402
import rules as rules_mod               # noqa: E402

MSK = timezone(timedelta(hours=3))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
FEED = "https://www.fl.ru/rss/all.xml"

# id категорий FL.ru подобраны перебором заголовков лент (см. probe в EVAL.md)
IT_CATS = {5: "Программирование", 25: "Веб-программирование", 96: "Сайты",
           83: "Базы данных", 52: "Администрирование"}
OTHER_CATS = {11: "Дизайн", 18: "Тексты", 20: "Аудио/Видео/Фото", 24: "Реклама и Маркетинг",
              48: "Инжиниринг", 13: "Переводы"}


def fetch(url, timeout=25):
    import gzip
    import time as _t
    import urllib.request
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            return raw.decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            _t.sleep(1.0)
    raise last


def _tag(block, tag):
    m = re.search(r"<%s>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>" % (tag, tag), block, re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def fetch_feed(cat_id, limit=60):
    raw = fetch("%s?category=%d" % (FEED, cat_id))
    out = []
    for block in re.findall(r"<item>(.*?)</item>", raw, re.S)[:limit]:
        title, desc = _tag(block, "title"), _tag(block, "description")
        if not (title or desc):
            continue
        out.append({"text": (title + ". " + desc).strip(), "url": _tag(block, "link"),
                    "time": _tag(block, "pubDate"), "source": "fl/rss/cat%d" % cat_id})
    return out


def fp(text):
    t = re.sub(r"[^а-яa-z0-9 ]", "", (text or "").lower().replace("ё", "е"))
    return hashlib.md5(re.sub(r"\s+", " ", t).strip()[:120].encode()).hexdigest()[:16]


def main():
    # ключи: профиль Discovery = 4 ниши (ИИ-агенты, чат-боты, парсинг, автоматизация)
    niches = json.load(open(os.path.join(HERE, "niches-50.json"), encoding="utf-8"))
    kws = {"strong": set(), "medium": set()}
    for k in ("ai-agents", "chatbots", "parsing", "business-automation"):
        b = wlp.build_kws(niches[k])
        kws["strong"].update(b["strong"])
        kws["medium"].update(b["medium"])
    kws["strong"], kws["medium"] = sorted(kws["strong"]), sorted(kws["medium"])
    naive_kws = kws["strong"] + kws["medium"]

    posts, seen = [], set()
    for cid, name in list(IT_CATS.items()) + list(OTHER_CATS.items()):
        try:
            items = fetch_feed(cid)
        except Exception as e:  # noqa: BLE001
            print("  [%s] feed error: %s" % (name, str(e)[:60]))
            continue
        for p in items:
            k = fp(p["text"])
            if k in seen:
                continue
            seen.add(k)
            p["rel"] = 1 if cid in IT_CATS else 0
            p["cat"] = name
            posts.append(p)
        print("  [%s] +%d" % (name, len(items)))

    rel_total = sum(p["rel"] for p in posts)
    print("\nПостов: %d · релевантных (IT): %d · контрольных: %d\n" % (len(posts), rel_total, len(posts) - rel_total))

    # контекст клиента: прогон с полными правилами и без правил v1.1
    new_whys = {"покупатель — компания (ООО/ИП)", "работа по договору / на р/с",
                "реквизиты в тексте (ИНН/ОГРН)", "домен компании в тексте",
                "учебный контекст клиента", "работа за обещание"}
    old_rules = [r for r in rules_mod.DEFAULT_RULES if r.get("why") not in new_whys]

    def score_all(rule_list):
        wlp._rules = type("R", (), {"apply_rules": staticmethod(
            lambda t, rl=rule_list: rules_mod.apply_rules(t, rl)), "describe": staticmethod(rules_mod.describe)})
        rows = []
        for p in posts:
            s, why = wlp.score_post(p, kws)
            p2 = dict(p, score=s, why=why)
            rows.append(p2)
        return rows

    def naive_hit(txt):
        return bool(wlp.kw_search(txt, naive_kws))

    def metrics(rows, thr):
        pred = [r for r in rows if r["score"] >= thr]
        tp = sum(r["rel"] for r in pred)
        prec = round(100 * tp / len(pred), 1) if pred else 0.0
        rec = round(100 * tp / rel_total, 1) if rel_total else 0.0
        return prec, rec, len(pred)

    for label, rule_list, modes in (
        ("с правилами v1.1 (контекст клиента)", rules_mod.DEFAULT_RULES,
         [("наивный поиск", -1), ("warm+ (>=30)", 30), ("hot (>=50)", 50), ("снайпер", None)]),
        ("без правил v1.1 (старый контекст)", old_rules,
         [("warm+ (>=30)", 30), ("hot (>=50)", 50), ("снайпер", None)]),
    ):
        print("=== %s ===" % label)
        rows = score_all(rule_list)
        for name, thr in modes:
            if name.startswith("наивный"):
                pred = [r for r in rows if naive_hit(r["text"])]
                tp = sum(r["rel"] for r in pred)
                prec = round(100 * tp / len(pred), 1) if pred else 0.0
                rec = round(100 * tp / rel_total, 1) if rel_total else 0.0
                print("  %-14s точность %5.1f%% · полнота %5.1f%% · отобрано %d" % (name, prec, rec, len(pred)))
            elif thr is not None:
                pr, rc, n = metrics(rows, thr)
                print("  %-14s точность %5.1f%% · полнота %5.1f%% · отобрано %d" % (name, pr, rc, n))
            else:
                for t in (60, 65, 70, 75, 80):
                    pr, rc, n = metrics(rows, t)
                    mark = " <-- >=90%" if pr >= 90 else ""
                    print("  %-14s точность %5.1f%% · полнота %5.1f%% · отобрано %d%s" % ("снайпер>=%d" % t, pr, rc, n, mark))
        # где теряем: ложные срабатывания снайпера + топ-30 для ручной разметки
        rows_sorted = sorted(rows, key=lambda r: -r["score"])[:30]
        print("  топ-12 с причинами:")
        for r in rows_sorted[:12]:
            print("    %3d [%s] %s || %s" % (r["score"], "IT " if r["rel"] else "НЕИТ", r["text"][:58], r["why"][:90]))
        out = [{"score": r["score"], "cat": r["cat"], "rel_guess": r["rel"], "text": r["text"],
                "url": r.get("url", ""), "why": r["why"]} for r in rows_sorted]
        json.dump(out, open(os.path.join(HERE, "reports", "top30-for-labels.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print()

    json.dump({"date": datetime.now(MSK).isoformat(), "posts": len(posts), "rel": rel_total,
               "rows": [{k: r.get(k) for k in ("rel", "cat", "score", "text")} for r in posts]},
              open(os.path.join(HERE, "reports", "precision-eval-%s.json" % datetime.now(MSK).strftime("%Y-%m-%d")), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("сырые прогоны: reports/precision-eval-*.json")


if __name__ == "__main__":
    main()
