#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Warm Lead Parser — обогащение лида по открытым госисточникам. stdlib-only, без ключей.

Идея: текст объявления — это анонимный пост. По нему нельзя понять, кто пишет:
живая это компания или однодневка, какая отрасль, есть ли у них деньги.
Открытые госисточники отвечают на это бесплатно, без регистрации и API-ключей.

Что достаём по одному ИНН / названию / домену:

  ЕГРЮЛ        yurлицо, ИНН/ОГРН/КПП, дата регистрации, РЕГИОН, РУКОВОДИТЕЛЬ с должностью,
               дата прекращения (если ликвидировано — сигнал «мёртвый бизнес»)
  Реестр МСП   категория (микро/малое/среднее), ОКВЭД + расшифровка ОТРАСЛИ, город,
               активность в реестре, лицензии, госконтракты
  trudvsem     бизнес живёт прямо сейчас: сколько активных вакансий, последняя,
               и EMAIL РАБОТОДАТЕЛЯ для первого контакта

Схема карточки: wlp.enrich/v1

Запуск:
  python integrations/enrich.py --selftest
  python integrations/enrich.py --inn 7707083893
  python integrations/enrich.py --name "Мастер-Ювелир"
  python integrations/enrich.py --text "Нужен бот, ИНН 7707083893, бюджет 50 тыс"
  python integrations/enrich.py --inn 7707083893 --json
"""
import gzip
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))
VERSION = "1.0.0"
SCHEMA = "wlp.enrich/v1"

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

EGRUL = "https://egrul.nalog.ru/"
RMSP = "https://rmsp.nalog.ru/search-proc.json"
TRUDVSEM = "https://opendata.trudvsem.ru/api/v1/vacancies/company/"

# Категории субъектов МСП (поле category в реестре)
MSP_CAT = {"1": "микропредприятие", "2": "малое предприятие", "3": "среднее предприятие"}


# --------------------------------------------------------------------- http
def _fetch(url, data=None, timeout=25, headers=None):
    """GET/POST с одним повтором и распаковкой gzip. Никаких ключей и токенов."""
    h = dict(UA)
    h.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if data else None
    last = None
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(url, data=body, headers=h)
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass
                return raw.decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001 — источник может просто лежать
            last = e
            if attempt == 0:
                time.sleep(0.4)
    raise last


# ----------------------------------------------------------------- нормализация
def norm(s: str) -> str:
    """Нижний регистр без ё: сравнение названий не должно спотыкаться на опечатках."""
    return re.sub(r"\s+", " ", (s or "").lower().replace("ё", "е")).strip()


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def inn_valid(inn: str) -> bool:
    """Контрольная сумма ИНН (10 — юрлицо, 12 — ИП).

    Защита от главной ошибки: любое 10-значное число в тексте не является ИНН.
    Без этой проверки парсер шлёт в госысточники случайные числа и получает мусор.
    """
    d = _digits(inn)
    if len(d) == 10:
        k = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        return sum(int(d[i]) * k[i] for i in range(9)) % 11 % 10 == int(d[9])
    if len(d) == 12:
        k1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        k2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        c1 = sum(int(d[i]) * k1[i] for i in range(10)) % 11 % 10 == int(d[10])
        c2 = sum(int(d[i]) * k2[i] for i in range(11)) % 11 % 10 == int(d[11])
        return c1 and c2
    return False


def ogrn_valid(ogrn: str) -> bool:
    """Контрольная сумма ОГРН (13) и ОГРНИП (15)."""
    d = _digits(ogrn)
    if len(d) == 13:
        return int(d[:12]) % 11 % 10 == int(d[12])
    if len(d) == 15:
        return int(d[:14]) % 13 % 10 == int(d[14])
    return False


DOMAIN_RE = re.compile(r"\b((?:[a-z0-9][a-z0-9-]{0,61}\.)+(?:ru|рф|com|net|org|io|pro|shop|store|online|site|tech))\b", re.I)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_RE = re.compile(r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
TG_RE = re.compile(r"(?<![@\w])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
INN_CTX_RE = re.compile(r"(?:инн|и\.н\.н\.?)\D{0,6}(\d{10}|\d{12})", re.I)
OGRN_CTX_RE = re.compile(r"(?:огрн(?:ип)?|о\.г\.р\.н\.?)\D{0,6}(\d{13}|\d{15})", re.I)
NAME_RE = re.compile(r"[«\"']([^«»\"']{3,60})[»\"']")

FREE_MAIL = ("mail.ru", "yandex.ru", "gmail.com", "bk.ru", "list.ru", "inbox.ru", "rambler.ru",
             "ya.ru", "internet.ru", "mail.com", "vk.com")


def extract_anchors(text: str) -> dict:
    """Вытащить из текста объявления всё, чем можно зацепиться для обогащения.

    ИНН/ОГРН берём только те, что прошли контрольную сумму — иначе шлём мусор.
    """
    t = text or ""
    anchors = {"inn": [], "ogrn": [], "email": [], "phone": [], "domain": [], "tg": [], "names": []}

    for m in INN_CTX_RE.finditer(t):
        if inn_valid(m.group(1)):
            anchors["inn"].append(_digits(m.group(1)))
    for m in OGRN_CTX_RE.finditer(t):
        if ogrn_valid(m.group(1)):
            anchors["ogrn"].append(_digits(m.group(1)))

    # ИНН без подписи: берём только валидные по контрольной сумме
    if not anchors["inn"]:
        for cand in re.findall(r"(?<!\d)(\d{10}|\d{12})(?!\d)", t):
            if inn_valid(cand):
                anchors["inn"].append(cand)
                break

    for e in EMAIL_RE.findall(t):
        if e.lower() not in anchors["email"]:
            anchors["email"].append(e.lower())
    for p in PHONE_RE.findall(t):
        d = _digits(p)
        if d not in anchors["phone"]:
            anchors["phone"].append(d)
    for d in DOMAIN_RE.findall(t):
        dl = d.lower().lstrip(".")
        if dl not in anchors["domain"] and not dl.endswith(("t.me", "vk.com")):
            anchors["domain"].append(dl)
    for h in TG_RE.findall(t):
        if h.lower() not in [x.lower() for x in anchors["tg"]]:
            anchors["tg"].append(h)
    for n in NAME_RE.findall(t):
        n = n.strip()
        if 3 <= len(n) <= 60 and not n.lower().startswith(("http", "www")):
            anchors["names"].append(n)

    for k in anchors:
        anchors[k] = anchors[k][:5]
    return {k: v for k, v in anchors.items() if v}


# --------------------------------------------------------------------- ЕГРЮЛ
def egrul_lookup(query: str, prefer_active: bool = True) -> dict | None:
    """Поиск в ЕГРЮЛ по ИНН/ОГРН/названию. Без ключей и капчи (проверено 2026-10-24).

    По названию ЕГРЮЛ отдаёт до 20 однофамильцев — среди них бывают ликвидированные.
    Поэтому выбираем запись осознанно: сначала точное совпадение названия, среди них —
    действующую (без даты прекращения). Иначе парсер покажет «живой» чужой бизнес.

    Возвращает: {name, short, inn, ogrn, kpp, reg_date, region, head, stop_date, kind,
                 rows, dead_in_result}
    """
    q = (query or "").strip()
    if not q:
        return None
    try:
        first = json.loads(_fetch(EGRUL, data={"query": q, "region": "", "PreventChromeAutocomplete": ""},
                                   headers={"X-Requested-With": "XMLHttpRequest",
                                            "Content-Type": "application/x-www-form-urlencoded"},
                                   timeout=35))
    except Exception:
        return None
    if first.get("captchaRequired"):
        return {"query": q, "error": "captcha_required"}
    tok = first.get("t")
    if not tok:
        return {"query": q, "error": "no_token"}
    time.sleep(1.0)  # ЕГРЮЛ роняет серию запросов без паузы
    try:
        res = json.loads(_fetch(f"https://egrul.nalog.ru/search-result/{tok}", timeout=35))
    except Exception:
        return None
    rows = res.get("rows") or []
    if not rows:
        return {"query": q, "found": False}
    dead_in_result = sum(1 for r in rows if r.get("e"))

    def _same_name(r) -> bool:
        """Совпадение по названию с точностью до кавычек, регистра и «ё»."""
        a = norm(re.sub(r"[«»\"']", "", r.get("c") or r.get("n") or ""))
        b = norm(re.sub(r"[«»\"']", "", q))
        if not b or len(b) < 4:
            return False
        return b in a or a in b

    pick = None
    if prefer_active:
        # 1) точное имя + действующая  2) точное имя (любая)  3) первая действующая  4) первая
        pool = [r for r in rows if _same_name(r)] or rows
        for r in pool:
            if not r.get("e"):
                pick = r
                break
        if pick is None:
            pick = next((r for r in rows if not r.get("e")), pool[0])
    else:
        pick = rows[0]

    return {
        "found": True,
        "query": q,
        "name": pick.get("n"),
        "short": pick.get("c"),
        "inn": pick.get("i"),
        "ogrn": pick.get("o"),
        "kpp": pick.get("p"),
        "reg_date": pick.get("r"),
        "region": pick.get("rn"),
        "head": pick.get("g"),
        "kind": pick.get("k"),          # ul — юрлицо, ip — ИП
        "stop_date": pick.get("e"),     # есть -> деятельность прекращена
        "rows": len(rows),
        "dead_in_result": dead_in_result,
    }


# ------------------------------------------------------------------ реестр МСП
def rmsp_lookup(query: str, page_size: int = 5) -> list:
    """Реестр МСП: категория, ОКВЭД с расшифровкой, город, активность, лицензии, госконтракты."""
    q = (query or "").strip()
    if not q:
        return []
    url = f"{RMSP}?query={urllib.parse.quote(q)}&mode=extended&page=1&pageSize={page_size}"
    try:
        d = json.loads(_fetch(url, timeout=25))
    except Exception:
        return []
    out = []
    for x in (d.get("data") or []):
        out.append({
            "name": x.get("name_ex"),
            "inn": x.get("inn"),
            "ogrn": x.get("ogrn"),
            "category": MSP_CAT.get(str(x.get("category")), x.get("category")),
            "okved": x.get("okved1"),
            "industry": x.get("okved1name"),
            "city": (f"{x.get('citytype', '')} {x.get('cityname', '')}").strip() or None,
            "active": str(x.get("is_active")) == "1",
            "licenses": str(x.get("has_licenses")) == "1",
            "gov_contracts": str(x.get("has_contracts")) == "1",
            "is_hitech": str(x.get("is_hitech")) == "1",
            "in_registry_from": x.get("dtregistry"),
            "in_registry_to": x.get("dtregistryout"),
        })
    return out


# -------------------------------------------------------------------- trudvsem
def trudvsem_company(ogrn: str, timeout: int = 60) -> dict | None:
    """Работа России: бизнес живёт сейчас? Сколько вакансий, последняя и EMAIL работодателя.

    Источник медленный (5–20 с) — таймаут выше общего.
    """
    o = _digits(ogrn)
    if len(o) not in (13, 15):
        return None
    try:
        d = json.loads(_fetch(f"{TRUDVSEM}{o}", timeout=timeout))
    except Exception:
        return {"ogrn": o, "error": "timeout_or_unavailable"}
    vacs = ((d.get("results") or {}).get("vacancies") or [])
    if not vacs:
        return {"ogrn": o, "found": False, "vacancies": 0}
    v = (vacs[0].get("vacancy") or {})
    comp = v.get("company") or {}
    last = None
    for item in vacs:
        vac = item.get("vacancy") or {}
        dt = vac.get("date_modify") or vac.get("creation-date")
        if dt and (last is None or dt > last):
            last = dt
    return {
        "found": True,
        "ogrn": o,
        "company": comp.get("name"),
        "email": (comp.get("email") or "").lower() or None,
        "site": comp.get("url") or None,
        "vacancies": (d.get("meta") or {}).get("total") or len(vacs),
        "last_vacancy": last,
        "sample_job": v.get("job-name"),
        "region": (v.get("region") or {}).get("name"),
    }


# --------------------------------------------------------------------- карточка
def _msp_match(msp_rows: list, inn: str = None, ogrn: str = None, region: str = None) -> dict | None:
    """Выбрать из выдачи МСП ту запись, что относится к нашему юрлицу (а не однофамильцу)."""
    if not msp_rows:
        return None
    for x in msp_rows:
        if inn and x.get("inn") == inn:
            return x
        if ogrn and x.get("ogrn") == ogrn:
            return x
    if region:
        for x in msp_rows:
            if x.get("city") and region.lower() in str(x.get("city")).lower():
                return x
    return msp_rows[0]


def enrich(text: str = None, inn: str = None, ogrn: str = None, name: str = None,
           with_vacancies: bool = True) -> dict:
    """Собрать карточку бизнеса по тому, что есть: текст объявления, ИНН, ОГРН или название.

    Деградирует грациозно: недоступный источник не ломает карточку, а помечается
    в поле sources (ok / error). Клиент всегда получает ответ и видит, чему верить.
    """
    anchors = extract_anchors(text) if text else {}
    inn = _digits(inn) if inn else (anchors.get("inn") or [None])[0]
    ogrn = _digits(ogrn) if ogrn else (anchors.get("ogrn") or [None])[0]
    name = name or (anchors.get("names") or [None])[0]
    sources = {}

    # 1. ЕГРЮЛ — основа: кто это и есть ли он вообще
    query = inn or ogrn or name
    egrul = egrul_lookup(query) if query else None
    sources["egrul"] = ("ok" if egrul and egrul.get("found")
                        else "not_found" if egrul is not None else "error")
    if egrul and egrul.get("found"):
        inn = inn or egrul.get("inn")
        ogrn = ogrn or egrul.get("ogrn")

    # 2. Реестр МСП — размер и отрасль
    msp = None
    if inn or name:
        rows = rmsp_lookup(inn or name)
        msp = _msp_match(rows, inn=inn, ogrn=ogrn, region=(egrul or {}).get("region"))
        sources["msp"] = ("ok" if msp else "not_found") if rows else "not_found"
    else:
        sources["msp"] = "no_query"

    # 3. trudvsem — жив ли бизнес сейчас + контакт
    tv = None
    if ogrn and with_vacancies:
        tv = trudvsem_company(ogrn)
        sources["trudvsem"] = ("ok" if tv and tv.get("found")
                               else "not_found" if tv else "skipped")
    else:
        sources["trudvsem"] = "skipped"

    # --- вывод: что мы реально узнали
    alive = None
    if egrul and egrul.get("found"):
        alive = not egrul.get("stop_date")
    if tv and tv.get("found") and tv.get("vacancies"):
        alive = True  # активные вакансии — сильнейший признак живой деятельности

    contact_email = None
    for e in (anchors.get("email") or []):
        contact_email = e
        break
    if not contact_email and tv and tv.get("email"):
        contact_email = tv["email"]

    signals = []
    if egrul and egrul.get("stop_date"):
        signals.append(f"деятельность прекращена {egrul['stop_date']} — мёртвый бизнес")
    if egrul and egrul.get("dead_in_result"):
        signals.append(f"среди однофамильцев в ЕГРЮЛ {egrul['dead_in_result']} ликвидированных")
    if msp and msp.get("category"):
        signals.append(f"МСП: {msp['category']}")
    if msp and msp.get("gov_contracts"):
        signals.append("есть госконтракты")
    if msp and msp.get("licenses"):
        signals.append("есть лицензии")
    if tv and tv.get("found") and tv.get("vacancies"):
        signals.append(f"активных вакансий: {tv['vacancies']}")
    if contact_email:
        dom = contact_email.split("@")[-1]
        signals.append("почта на своей домене" if dom not in FREE_MAIL else "почта на бесплатном домене")

    card = {
        "schema": SCHEMA,
        "version": VERSION,
        "checked_at": datetime.now(MSK).strftime("%Y-%m-%d %H:%M MSK"),
        "query": {"inn": inn, "ogrn": ogrn, "name": name},
        "anchors": anchors,
        "found": bool(egrul and egrul.get("found")) or bool(msp),
        "alive": alive,
        "company": {
            "name": ((egrul or {}).get("short") or (egrul or {}).get("name")
                     or (msp or {}).get("name")),
            "inn": inn,
            "ogrn": ogrn,
            "kpp": (egrul or {}).get("kpp"),
            "reg_date": (egrul or {}).get("reg_date"),
            "region": (egrul or {}).get("region") or (msp or {}).get("city"),
            "head": (egrul or {}).get("head"),
            "kind": (egrul or {}).get("kind"),
            "category": (msp or {}).get("category"),
            "industry": (msp or {}).get("industry"),
            "okved": (msp or {}).get("okved"),
            "stop_date": (egrul or {}).get("stop_date"),
        },
        "contact": {"email": contact_email,
                    "phone": (anchors.get("phone") or [None])[0],
                    "site": (tv or {}).get("site") or (anchors.get("domain") or [None])[0],
                    "tg": (anchors.get("tg") or [None])[0]},
        "activity": {"vacancies": (tv or {}).get("vacancies"),
                     "last_vacancy": (tv or {}).get("last_vacancy"),
                     "sample_job": (tv or {}).get("sample_job")},
        "signals": signals,
        "sources": sources,
        "egrul_rows": (egrul or {}).get("rows"),
    }
    return card


def format_card(card: dict) -> str:
    """Карточка в одну markdown-вставку для отчёта."""
    c = card.get("company") or {}
    ct = card.get("contact") or {}
    act = card.get("activity") or {}
    if not card.get("found"):
        return f"- ⚪ не найден в госреестрах (запрос: {card['query'].get('inn') or card['query'].get('name') or '—'})"
    flag = "🟢" if card.get("alive") else ("⚫" if card.get("alive") is False else "⚪")
    lines = [f"- {flag} **{c.get('name') or '—'}** · ИНН {c.get('inn') or '—'}"]
    bits = []
    if c.get("industry"):
        bits.append(c["industry"])
    if c.get("category"):
        bits.append(c["category"])
    if c.get("region"):
        bits.append(c["region"])
    if c.get("reg_date"):
        bits.append(f"с {c['reg_date']}")
    if bits:
        lines.append(f"  - {' · '.join(bits)}")
    if c.get("head"):
        lines.append(f"  - {c['head']}")
    if c.get("stop_date"):
        lines.append(f"  - ⚫ **деятельность прекращена {c['stop_date']}**")
    if ct.get("email"):
        lines.append(f"  - контакт: {ct['email']}")
    if act.get("vacancies"):
        lines.append(f"  - вакансий сейчас: {act['vacancies']}")
    if card.get("signals"):
        lines.append(f"  - сигналы: {'; '.join(card['signals'])}")
    bad = [k for k, v in (card.get("sources") or {}).items() if v not in ("ok",)]
    if bad:
        lines.append(f"  - _источники не ответили или пусто: {', '.join(bad)}_")
    return "\n".join(lines)


# ------------------------------------------------------------------- selftest
def _selftest() -> bool:
    """Без сети: проверяем то, что чаще всего ломается — разбор якорей и контрольные суммы."""
    cases = [
        # (текст, ожидаемые ключи якорей, замечание)
        ("Сбербанк ИНН 7707083893 просит бота", {"inn": ["7707083893"]}, "валидный ИНН юрлица"),
        ("мой ИНН 7707083894 — ошибка в последней цифре", {}, "невалидная контрольная — не берём"),
        ("компания «Мастер-Ювелир», tel +7 495 123-45-67", {"names": ["Мастер-Ювелир"], "phone": ["74951234567"]},
         "название в кавычках и телефон"),
        ("пишите на info@master-juvelir.ru или @juvelir_master", {"email": ["info@master-juvelir.ru"], "tg": ["juvelir_master"]},
         "почта и телеграм"),
        ("наш сайт https://www.master-yuvelir.ru/price", {"domain": ["www.master-yuvelir.ru"]}, "домен"),
        ("ОГРН 1027700132195", {"ogrn": ["1027700132195"]}, "валидный ОГРН"),
        ("ОГРН 1027700132196", {}, "невалидный ОГРН — не берём"),
    ]
    ok = 0
    for txt, want, note in cases:
        got = extract_anchors(txt)
        good = all(got.get(k) and any(v in got[k] for v in vals) or (not vals and not got.get(k, []))
                   for k, vals in want.items())
        # отдельная проверка «не должно быть лишнего»
        if not want:
            good = not any(got.get(k) for k in ("inn", "ogrn"))
        mark = "OK " if good else "FAIL"
        if good:
            ok += 1
        print(f"[{mark}] {note:42} → {json.dumps(got, ensure_ascii=False)[:110]}")

    # контрольные суммы отдельно
    sums = [(inn_valid("7707083893"), True, "ИНН Сбербанка валиден"),
            (inn_valid("7707083894"), False, "ИНН с ошибкой невалиден"),
            (ogrn_valid("1027700132195"), True, "ОГРН Сбербанка валиден")]
    for got, want, note in sums:
        mark = "OK " if got == want else "FAIL"
        if got == want:
            ok += 1
        print(f"[{mark}] {note}")
    total = len(cases) + len(sums)
    print(f"SELFTEST: {ok}/{total}")
    return ok == total


# ------------------------------------------------------------------------ CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Обогащение лида по открытым госисточникам (без ключей)")
    ap.add_argument("--inn", help="ИНН юрлица или ИП")
    ap.add_argument("--ogrn", help="ОГРН / ОГРНИП")
    ap.add_argument("--name", help="название компании")
    ap.add_argument("--text", help="текст объявления — якоря вытащим сами")
    ap.add_argument("--json", action="store_true", help="вывод JSON (схема wlp.enrich/v1)")
    ap.add_argument("--no-vacancies", action="store_true", help="не ходить в trudvsem (он медленный)")
    ap.add_argument("--selftest", action="store_true", help="проверка без сети")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if _selftest() else 1
    if not any((a.inn, a.ogrn, a.name, a.text)):
        ap.print_help()
        return 1
    card = enrich(text=a.text, inn=a.inn, ogrn=a.ogrn, name=a.name, with_vacancies=not a.no_vacancies)
    print(json.dumps(card, ensure_ascii=False, indent=2) if a.json else format_card(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
