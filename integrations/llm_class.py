#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM-слой Warm Lead Parser — второй проход по кандидатам (v1.2).

Зачем: ключи и правила отсекают 80% потока дёшево, но потолок точности на сырой
ленте — 65–75% (замер 4, EVAL.md): консультации, менторство и «клип с помощью ИИ»
от заказов словами не отличить. Второй проход читает текст кандидата целиком и
решает по смыслу — это стандартная двухступенчатая схема, LLM-слой стоит копейки
(30 кандидатов ~ 5–10К токенов на прогон).

Конфиг (только env, ничего в репо):
  WLP_LLM_KEY   — ключ Bearer (обязателен; без него слой молча выключен)
  WLP_LLM_BASE  — base URL OpenAI-совместимого API (по умолчанию router.bynara.id/v1)
  WLP_LLM_MODEL — модель (по умолчанию deepseek-v4.1-flash)

Запуск:
  python integrations/llm_class.py --selftest     # без сети, фейковый транспорт
  python integrations/llm_class.py "Нужен бот" "Нужен ментор по ИИ"   # живой вызов
"""
import json
import os
import re
import urllib.request

VERSION = "1.0.0"
SCHEMA = "wlp.llm/v1"
DEFAULT_BASE = "https://router.bynara.id/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"
TIMEOUT = 45

SYSTEM = (
    "Ты фильтр заказов для фрилансера-разработчика. Тебе дают тексты объявлений. "
    "LEAD — заказ на разработку или внедрение ИТ-решения (бот, парсер, автоматизация, "
    "сайт, интеграция, скрипт, приложение), который фрилансер может взять и получить оплату. "
    "NOT — консультации, менторство, обучение, курсы, контент и видео, дизайн-полиграфия, "
    "бухгалтерия и юристы, «ищу команду/работу», продажа сервисов, размышления без задачи. "
    "Сомневаешься — NOT. Ответ строго JSON: [{\"id\":0,\"v\":\"LEAD\"|\"NOT\",\"why\":\"до 8 слов\"}]"
)


def llm_available():
    return bool(os.environ.get("WLP_LLM_KEY"))


def _config():
    return (os.environ.get("WLP_LLM_BASE") or DEFAULT_BASE,
            os.environ.get("WLP_LLM_KEY"),
            os.environ.get("WLP_LLM_MODEL") or DEFAULT_MODEL)


def _post(url, key, body, timeout=TIMEOUT):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _parse(content):
    """Достать JSON-массив из ответа модели (переносит markdown-заборы)."""
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        raise ValueError("нет JSON в ответе")
    out = {}
    for item in json.loads(m.group(0)):
        v = str(item.get("v", "")).upper()
        out[int(item.get("id", -1))] = {"v": "LEAD" if v == "LEAD" else "NOT",
                                        "why": str(item.get("why", ""))[:60]}
    return out


def classify(texts, transport=_post):
    """Классифицировать список текстов. Возвращает список {'v','why'} в том же порядке.

    Транспорт подменяется в selftest; без ключа возвращает [{v:'SKIP', why:'нет ключа'}].
    """
    if not texts:
        return []
    base, key, model = _config()
    if not key:
        return [{"v": "SKIP", "why": "нет ключа"} for _ in texts]
    payload = "\n".join("%d) %s" % (i, (t or "")[:400].replace("\n", " ")) for i, t in enumerate(texts))
    body = {"model": model, "temperature": 0.1, "max_tokens": 900,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": "Тексты:\n" + payload}]}
    raw = transport(base + "/chat/completions", key, body)
    content = raw["choices"][0]["message"]["content"]
    try:
        parsed = _parse(content)
    except Exception:  # noqa: BLE001 — битый ответ деградирует в NOT, не роняет прогон
        return [{"v": "NOT", "why": "битый ответ модели"} for _ in texts]
    return [parsed.get(i, {"v": "NOT", "why": "нет ответа по строке"}) for i in range(len(texts))]


def _selftest():
    ok = 0
    # 1. без ключа — SKIP, не падение
    os.environ.pop("WLP_LLM_KEY", None)
    r = classify(["Нужен бот"])
    if r[0]["v"] == "SKIP":
        ok += 1
        print("[OK] без ключа: SKIP, парсер работает как раньше")
    else:
        print("[FAIL] без ключа ожидался SKIP")
    # 2. парсинг ответа с markdown-забором
    def fake_post(url, key, body):
        return {"choices": [{"message": {"content":
            "```json\n[{\"id\":0,\"v\":\"LEAD\",\"why\":\"заказ на бота, бюджет\"},"
            "{\"id\":1,\"v\":\"NOT\",\"why\":\"менторство, не заказ\"}]```"}}]}
    os.environ["WLP_LLM_KEY"] = "test"
    r = classify(["Нужен бот для ООО, бюджет 80 тыс", "Ищу ментора по ИИ"], transport=fake_post)
    if r[0]["v"] == "LEAD" and r[1]["v"] == "NOT" and "ментор" in r[1]["why"]:
        ok += 1
        print("[OK] парсинг ответа и вердикты: %s / %s" % (r[0]["v"], r[1]["v"]))
    else:
        print("[FAIL] вердикты: %s" % r)
    # 3. битый ответ — NOT с причиной, не исключение
    def bad_post(url, key, body):
        return {"choices": [{"message": {"content": "мимо JSON"}}]}
    r = classify(["текст"], transport=bad_post)
    if r[0]["v"] == "NOT":
        ok += 1
        print("[OK] битый ответ деградирует в NOT, не роняет прогон")
    else:
        print("[FAIL] битый ответ: %s" % r)
    os.environ.pop("WLP_LLM_KEY", None)
    print("SELFTEST: %d/3" % ok)
    return ok == 3


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(0 if _selftest() else 1)
    args = [a for a in sys.argv[1:] if a.strip()]
    if not args:
        print("использование: llm_class.py [--selftest] \"текст 1\" \"текст 2\" ...")
        raise SystemExit(1)
    for t, r in zip(args, classify(args)):
        print("[%s] %s :: %s" % (r["v"], r["why"], t[:60]))
