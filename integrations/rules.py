#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Движок правил для Warm Lead Parser — Exclude / Penalty / Boost с приоритетами.

Идея взята у `Riniba/TelegramMonitor` (329★, Apache-2.0): правила мониторинга с
действиями и приоритетами вместо плоского списка стоп-слов, плюс четыре типа
сопоставления (exact / contains / regex / fuzzy).

Зачем это нужно. Плоский фильтр умеет только «выбросить» — и режет настоящие заказы
вместе с шумом. Приоритет решает конфликт: **сильный сигнал покупки перебивает
мягкий признак шума.** Пример: «Ищем подрядчика в команду, нужен бот за срочно,
бюджет 80 тыс» — тут есть и маркер найма, и явная покупка; плоский фильтр выбросит
заказ, приоритетный — оставит с пометкой.

Схема: `wlp.rules/v1`
"""
import json
import os
import re
from difflib import SequenceMatcher

VERSION = "1.0.0"
SCHEMA = "wlp.rules/v1"

# Приоритет = насколько правило сильно. 100 — «выбрасывать всегда», 10 — «шепнул».
# Действия: drop (выбросить), penalty (минус к скору), boost (плюс к скору).
DEFAULT_RULES = [
    # --- соискатели и найм: выбрасывать
    {"p": 100, "type": "regex", "v": r"#резюме|ищу работу|в поиске работы|резюме [a-z@]",
     "act": "drop", "why": "резюме соискателя"},
    {"p": 96, "type": "regex",
     "v": r"ваканси|в штат|з/п|зарплат|оклад|собеседован|тк рф|резюме направляйте|оплата по грейду",
     "act": "drop", "why": "вакансия (наём, не заказ)"},
    # Мягкие маркеры найма по одному встречаются и в настоящем заказе («ищем подрядчика»).
    # Отсекаем не по одному слову, а по их количеству: два и больше — это вакансия.
    # И даже тогда это штраф, а не выбрасывание: сильный сигнал покупки может перевесить.
    {"p": 80, "type": "contains", "min": 2,
     "v": ["в команду", "ищем ", "требуется", "опыт работы", "портфолио", "рассматриваем", "кандидат",
           "full-time", "фултайм", "part-time", "middle", "senior", "junior", "head of"],
     "act": "penalty", "w": -15, "why": "язык найма (2+ маркера)"},

    # --- заведомо не наш заказ
    {"p": 90, "type": "contains", "v": ["курсовая", "дипломная", "лабораторная", "студенческая работ"],
     "act": "drop", "why": "учебная задача"},
    {"p": 88, "type": "contains", "v": ["за бесплатно", "для портфолио бесплатно", "без оплаты", "за отзыв"],
     "act": "drop", "why": "без оплаты"},
    {"p": 86, "type": "contains", "v": ["заработок в интернете без вложений", "не развод", "лёгкие деньги", "пассивный доход"],
     "act": "drop", "why": "скам-приманка"},

    # --- давят на цену: не выбрасывать, но понижать
    {"p": 55, "type": "contains", "v": ["подешевле", "недорого", "дёшево", "минимальный бюджет", "бюджет ограничен"],
     "act": "penalty", "w": -15, "why": "давят на цену"},
    {"p": 50, "type": "contains", "v": ["по бартеру", "оплата частями потом", "процент от прибыли"],
     "act": "penalty", "w": -10, "why": "оплата не деньгами"},

    # --- сигналы покупки: поднимать (и они перебивают мягкий шум)
    {"p": 70, "type": "contains", "v": ["нужен", "нужна", "нужно", "требует", "ищу исполнителя", "ищу подрядчика", "закажу"],
     "act": "boost", "w": 8, "why": "явная потребность"},
    {"p": 72, "type": "contains", "v": ["бюджет", "оплата", "готов платить", "по договорённости с бюджетом"],
     "act": "boost", "w": 10, "why": "названы деньги"},
    {"p": 74, "type": "contains", "v": ["можно начать сегодня", "готов начать", "когда сможете приступить"],
     "act": "boost", "w": 10, "why": "готов к старту"},
    {"p": 60, "type": "contains", "v": ["срочно", "asap", "горит", "нужно сегодня", "до конца недели"],
     "act": "boost", "w": 12, "why": "срочность"},
    {"p": 58, "type": "contains", "v": ["есть тз", "тз есть", "описание готово", "всё расскажу в лс"],
     "act": "boost", "w": 8, "why": "проработанный запрос"},
    {"p": 45, "type": "contains", "v": ["внедрить ии", "нейросет", "нейронк", "ии-агент", "автоматизац"],
     "act": "boost", "w": 6, "why": "профильная формулировка"},
]

FUZZY_THRESHOLD = 0.86  # ниже — начинаются ложные срабатывания на коротких словах


def load_rules(path=None):
    """Правила: по умолчанию встроенные, при наличии рядом rules.json — свои.

    Свой файл нужен, чтобы править фильтр под нишу без правки кода:
    достаточно положить рядом rules.json со списком правил в том же формате.
    """
    base = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")
    if path or os.path.exists(base):
        try:
            data = json.load(open(base, encoding="utf-8"))
            rules = data.get("rules") if isinstance(data, dict) else data
            if isinstance(rules, list) and rules:
                return rules
        except Exception:
            pass
    return list(DEFAULT_RULES)


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def _fuzzy_any(hay, words, threshold=FUZZY_THRESHOLD):
    """Нечёткое совпадение: ловит опечатки и словоформы, которые regex пропускает.

    Сравниваем поморфемно: если хотя бы одно слово текста похоже на образец выше
    порога — правило сработало. Порог завышен сознательно: на коротких словах
    нечёткое сравнение врёт («бот» похоже на «кот»).
    """
    for w in words:
        w = _norm(w)
        if len(w) < 4:
            continue
        for token in re.findall(r"[а-яa-z0-9\-]{4,}", hay):
            if abs(len(token) - len(w)) > 3:
                continue
            if SequenceMatcher(None, token, w).ratio() >= threshold:
                return True
    return False


def match_rule(rule, text):
    """Сработало ли правило на тексте."""
    t = _norm(text)
    if not t:
        return False
    typ = rule.get("type", "contains")
    val = rule.get("v")
    if typ == "regex":
        try:
            return bool(re.search(val, t))
        except re.error:
            return False
    values = val if isinstance(val, list) else [val]
    if typ == "exact":
        return t in [_norm(v) for v in values]
    if typ == "fuzzy":
        return _fuzzy_any(t, values)
    # contains — по умолчанию; ищем подстрокой (для стоп-слов это верный выбор:
    # ложный минус дешевле ложного плюса). Параметр min требует N совпадений:
    # один маркер найма бывает и в настоящем заказе, два и больше — уже вакансия.
    if typ == "contains":
        found = [v for v in values if v and _norm(v) in t]
        return len(found) >= int(rule.get("min", 1))
    return any(_norm(v) in t for v in values if v)


def apply_rules(text, rules=None):
    """Применить правила к тексту поста.

    Возвращает (drop, delta, hits):
      drop  — причина отсева или None; при отсеве пост дальше не оценивается
      delta — суммарная поправка к скору (может быть отрицательной)
      hits  — сработавшие правила, для объяснения в отчёте

    Приоритет работает так: если сработало правило-отсев, оно выбрасывает пост —
    но только когда нет более приоритетного сигнала покупки. «Ищем в команду, нужен
    бот, бюджет 80 тыс» — это заказ, а не вакансия, даже при слове «ищем».
    """
    rules = rules if rules is not None else DEFAULT_RULES
    hits = []
    for r in rules:
        if match_rule(r, text):
            hits.append(r)
    if not hits:
        return None, 0, []

    drops = [r for r in hits if r.get("act") == "drop"]
    boosts = [r for r in hits if r.get("act") == "boost"]
    if drops:
        top_drop = max(r.get("p", 0) for r in drops)
        top_boost = max((r.get("p", 0) for r in boosts), default=0)
        # сигнал покупки приоритетнее признака шума
        if top_boost > top_drop:
            drops = []
        else:
            worst = max(drops, key=lambda r: r.get("p", 0))
            return worst.get("why", "правило"), 0, hits

    delta = sum(int(r.get("w", 0)) for r in hits if r.get("act") in ("penalty", "boost"))
    return None, delta, hits


def describe(hits, keep=4):
    """Короткое объяснение для отчёта."""
    if not hits:
        return ""
    parts = []
    for r in sorted(hits, key=lambda x: -x.get("p", 0))[:keep]:
        mark = {"drop": "✕", "penalty": "−", "boost": "+"}.get(r.get("act"), "·")
        parts.append(f"{mark}{r.get('why', '?')}")
    return "; ".join(parts)


def _selftest():
    """Правила — самая рискованная часть: ошибка тут тихо съедает заказы или
    пропускает вакансии. Поэтому проверяем и то, что должно отсечься, и то,
    что обязано остаться."""
    cases = [
        # (текст, ожидаемый отсев или None, замечание)
        ("Ищем разработчика в команду, з/п от 150000, требуется опыт 3 года", "вакансия (наём, не заказ)", "вакансия отсекается"),
        ("Резюме Python-разработчика, ищу работу", "резюме соискателя", "резюме отсекается"),
        ("Нужна лабораторная по базам данных, срочно", "учебная задача", "учёбка отсекается"),
        ("Сделаю бота за отзыв, без оплаты", "без оплаты", "бесплатники отсекаются"),
        ("Собрал чат-бота и заработал 27000, это не развод", "скам-приманка", "скам отсекается"),
        # главное: настоящий заказ не должен отсекаться никогда
        ("Ищем подрядчика в команду: нужен бот для приёма заявок, бюджет 80000", None,
         "заказ перебивает маркер найма"),
        ("Нужен ИИ-агент для учёта, бюджет 50 тыс, готов начать сегодня", None, "обычный заказ проходит"),
        ("Нужен парсер сайта, срочно, есть ТЗ", None, "заказ со срочностью проходит"),
        ("Есть задача: бот для клиники, по договорённости с бюджетом", None, "заказ без точной суммы проходит"),
    ]
    ok = 0
    for txt, want_drop, note in cases:
        drop, delta, hits = apply_rules(txt)
        got_ok = (drop == want_drop)
        if got_ok:
            ok += 1
        mark = "OK " if got_ok else "FAIL"
        print(f"[{mark}] {note:38} drop={str(drop)[:26]:26} delta={delta:+3d} | {describe(hits)[:52]}")

    # проверка приоритета отдельно
    checks = [
        (apply_rules("нужен бот, бюджет 50 тыс")[1] > 0, True, "сигналы покупки дают плюс"),
        (apply_rules("ищу работу, нужен бот")[0] is not None, True, "резюме бьёт потребность (100 > 70)"),
        (apply_rules("подешевле бы, бюджет минимальный")[1] < 0, True, "давление на цену даёт минус"),
    ]
    for got, want, note in checks:
        mark = "OK " if got == want else "FAIL"
        if got == want:
            ok += 1
        print(f"[{mark}] {note}")
    total = len(cases) + len(checks)
    print(f"SELFTEST: {ok}/{total}")
    return ok == total


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(0 if _selftest() else 1)
    txt = sys.stdin.read() if not sys.argv[1:] else " ".join(sys.argv[1:])
    drop, delta, hits = apply_rules(txt, load_rules())
    print(json.dumps({"schema": SCHEMA, "drop": drop, "delta": delta,
                      "hits": [{"why": h.get("why"), "act": h.get("act"), "p": h.get("p")} for h in hits],
                      "explain": describe(hits)}, ensure_ascii=False, indent=2))
