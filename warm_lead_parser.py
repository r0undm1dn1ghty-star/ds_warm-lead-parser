#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discovery Warm Lead Parser v2.7 — мульти-нишевой движок.
Один файл, stdlib-only. 53 ниши из niches-50.json (все источники фактчек-2026-09-11).

Запуск:
  python warm_lead_parser.py                        # все ниши (быстрый режим: FL+WL+TG+биржи, без Авито)
  python warm_lead_parser.py --niche chatbots       # одна ниша (полный режим с Kwork)
  python warm_lead_parser.py --list                 # список ниш
  python warm_lead_parser.py --full chatbots        # одна ниша, все источники включая Авито (3-4 мин)
Источники: FL.ru RSS (60 заказов с текстами одним запросом) + HTML-категория как добор · Weblancer · Kwork (через r.jina.ai) ·
           TG-каналы (t.me/s/ превью) · Kadrof · poisk-pro.ru · searchengines.guru ·
           illustrators.ru · Авито (Camoufox стелс, market-signal).
freelance.ru исключён: пер-нишевой фильтрации нет (фактчек 09-11).
"""
import json, re, html, sys, os, time, socket
import urllib.request
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

MSK = timezone(timedelta(hours=3))
# Полный набор браузерных заголовков: часть источников (kadrof, illustrators)
# отдаёт контент только при полном наборе — иначе соединение висит до таймаута.
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
      "Accept-Encoding": "gzip, deflate"}
HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------- обогащение по госисточникам
# Опционально (флаг --enrich): по тексту объявления вытаскиваем ИНН/название/домен и
# добираем в ЕГРЮЛ, реестре МСП и на «Работе России» — кто это, какая отрасль,
# жив ли бизнес, ФИО руководителя и рабочий email. Без ключей и регистраций.
sys.path.insert(0, os.path.join(HERE, "integrations"))
try:
    import enrich as _enrich
except Exception:  # модуль мог не доехать — парсер обязан работать и без него
    _enrich = None

# ---------------------------------------------------------------- конфиг ниш
def load_niches():
    for fn in ("niches-50.json",):
        p = os.path.join(HERE, fn)
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))
    print("!! niches-50.json не найден рядом с движком"); sys.exit(1)

# ---------------------------------------------------------------- http
# v2.3: единый потолок ожидания на сокете. Один зависший источник (FL.ru по
# таймауту) не должен сжрать бюджет всего прогона.
socket.setdefaulttimeout(12)

def get(u, timeout=12, headers=None):
    """GET с одним повтором: первый контакт со свежего соединения
    у части источников может подвиснуть — повтор проходит мгновенно."""
    last = None
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(u, headers=headers or UA)
            r = urllib.request.urlopen(req, timeout=timeout)
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip as _gz
                data = _gz.decompress(data)
            return data.decode("utf-8", "ignore")
        except Exception as e:
            last = e
            if attempt == 0:
                time.sleep(0.4)
    raise last

# ---------------------------------------------------------------- сборщики
def fetch_fl(category, niche_key):
    """FL.ru через HTML категории. Оставлен как fallback: регулярно падает в таймаут."""
    return fetch_fl_html(category, niche_key)


def _rss_time(s):
    """RSS pubDate ('Mon, 26 Oct 2026 18:50:35 GMT') -> ISO в MSK."""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).astimezone(MSK).isoformat()
    except Exception:
        return None


def fetch_fl_rss(niche_key="mix", limit=80):
    """FL.ru через официальную RSS-ленту (v2.7).

    Почему это лучше HTML-категорий: одна лента отдаёт сразу 60 заказов с заголовком,
    описанием и датой, официальным каналом — без парсинга разметки и без тех таймаутов,
    на которых прежний сборщик падал в каждом втором прогоне.
    """
    raw = get("https://www.fl.ru/rss/all.xml", timeout=15)

    def _tag(block, tag):
        m = re.search(r"<%s>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>" % (tag, tag), block, re.S)
        if not m:
            return ""
        return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()

    out = []
    for block in re.findall(r"<item>(.*?)</item>", raw, re.S)[:limit]:
        title = _tag(block, "title")
        desc = _tag(block, "description")
        link = _tag(block, "link")
        if not (title or desc):
            continue
        out.append({
            "source": "fl/rss",
            "text": (title + ". " + desc).strip() if title else desc,
            "url": link,
            "time": _rss_time(_tag(block, "pubDate")),
            "niche": niche_key,
        })
    return out


def dedup_posts(posts):
    """Схлопнуть один и тот же заказ, найденный на нескольких площадках (v2.7).

    Один заказ часто висит на бирже, в RSS и в трёх Telegram-каналах: сейчас это
    четыре разных лида и четыре раза потраченное внимание. Ключ — отпечаток
    начала текста без пунктуации; в дублирующей записи остаётся ссылка на источник,
    чтобы было видно, где ещё искать отклик.
    """
    seen, out, merged = {}, [], 0
    for p in posts:
        fp = re.sub(r"[^а-яёa-z0-9 ]", "", (p.get("text") or "").lower().replace("ё", "е"))
        fp = re.sub(r"\s+", " ", fp).strip()[:70]
        if not fp:
            out.append(p)
            continue
        if fp in seen:
            host = seen[fp]
            url = p.get("url")
            if url and url not in host.setdefault("also_on", []):
                host["also_on"].append(url)
            merged += 1
        else:
            seen[fp] = p
            out.append(p)
    return out, merged


def fetch_fl_html(category, niche_key):
    """FL.ru: карточки проектов. Читаем заголовок + бюджет + описание,
    иначе скоринг не видит деньги и детали (проверено на живых данных)."""
    out = []
    raw = get(f"https://www.fl.ru/projects/category/{category}/")
    seen = set()
    for card in re.split(r'id="project-item', raw)[1:]:
        mt = re.search(r'b-post__title[^"]*"[^>]*>\s*<a[^>]*href="(/projects/\d+/[^"]+)"[^>]*>([^<]+)</a>', card)
        if not mt:
            continue
        u, title = mt.group(1), mt.group(2)
        if u in seen:
            continue
        seen.add(u)
        mp = re.search(r'b-post__price[^>]*>\s*(?:<span[^>]*>)?([^<]{0,60})', card)
        md = re.search(r'b-post__txt[^>]*>([^<]{0,600})', card)
        parts = [html.unescape(re.sub(r'\s+', ' ', title)).strip()]
        if mp:
            parts.append(html.unescape(re.sub(r'\s+', ' ', mp.group(1))).strip())
        if md:
            parts.append(html.unescape(re.sub(r'\s+', ' ', md.group(1))).strip())
        text = " | ".join(p for p in parts if p)
        if len(text) < 20:
            continue
        out.append({"source": f"fl.ru/{niche_key}", "time": "",
                    "text": text[:600],
                    "url": "https://www.fl.ru" + u})
    return out

def fetch_weblancer(slug, niche_key):
    """Weblancer: лента заказов /freelance/ (+ фильтр по нише на стороне клиента).
    Категорийные слаги переехали — берём общую ленту, нишу фильтрует скоринг."""
    out = []
    raw = get("https://www.weblancer.net/freelance/", timeout=25)
    seen = set()
    for m in re.finditer(
            r'<h2[^>]*>\s*<a[^>]*href="(/freelance/[^"]+/)"[^>]*>([^<]+)</a>\s*</h2>'
            r'\s*</div>\s*<p[^>]*>(.*?)</p>', raw, re.S):
        u, t, d = m.group(1), m.group(2), m.group(3)
        if u in seen:
            continue
        seen.add(u)
        title = html.unescape(re.sub(r'\s+', ' ', t)).strip()
        desc = html.unescape(re.sub(r'<[^>]+>', ' ', d))
        desc = re.sub(r'\s+', ' ', desc).strip()
        tail = raw[m.end():m.end() + 1500]
        mb = re.search(r'([\d\s]{3,9})\s*(?:₽|руб)', tail)
        parts = [title]
        if mb:
            parts.append(re.sub(r'\s+', ' ', mb.group(0)).strip())
        if desc:
            parts.append(desc[:300])
        text = " | ".join(x for x in parts if x)
        if len(text) < 20:
            continue
        out.append({"source": f"weblancer/{niche_key}", "time": "", "text": text[:600],
                    "url": "https://www.weblancer.net" + u})
    return out

def fetch_kwork(cat_id, niche_key):
    """Kwork: страница категории отдаёт проекты встроенным JSON (wants).
    Парсим JSON-структуру напрямую — заголовок, бюджет, описание."""
    out = []
    raw = ""
    try:
        raw = get(f"https://kwork.ru/projects?c={cat_id}", timeout=25)
    except Exception:
        pass
    if '"wants"' not in raw:
        try:
            req = urllib.request.Request(f"https://r.jina.ai/https://kwork.ru/projects?c={cat_id}",
                                         headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
        except Exception:
            return out
    seen = set()
    i = raw.find('"wants":[')
    if i >= 0:
        # декодируем ровно один JSON-массив начиная с '[' (корректно снимает \uXXXX)
        arr = raw[raw.find('[', i):]
        try:
            wants = json.JSONDecoder().raw_decode(arr)[0]
        except Exception:
            wants = []
        for w in wants if isinstance(wants, list) else []:
            if not isinstance(w, dict):
                continue
            pid = str(w.get("id") or "")
            title = (w.get("title") or w.get("name") or "").strip()
            desc = (w.get("description") or "").strip()
            price = w.get("possiblePriceLimit") or 0
            if not pid or pid in seen or len(title) < 8:
                continue
            seen.add(pid)
            try:
                budget = "до %s ₽" % format(int(price), ",d").replace(",", " ") if int(price) > 0 else ""
            except Exception:
                budget = ""
            parts = [title, budget, desc[:400]]
            text = " | ".join(x for x in parts if x)
            out.append({"source": f"kwork/{niche_key}", "time": "", "text": text[:600],
                        "url": "https://kwork.ru/projects/" + pid})
    if not out:
        for t, pid in re.findall(r'\[([^\]]{20,100})\]\(https://kwork\.ru/projects/(\d+)[^)]*\)', raw):
            if pid in seen or len(t.strip()) < 20:
                continue
            seen.add(pid)
            out.append({"source": f"kwork/{niche_key}", "time": "", "text": t.strip(),
                        "url": "https://kwork.ru/projects/" + pid})
    return out

def fetch_poisk_pro(pages=2, niche_key="engineering"):
    """poisk-pro.ru — биржа проектировщиков: заявки с датами и бюджетами 20-233К₽ (фактчек 09-11)."""
    out = []
    for page in range(1, pages + 1):
        try:
            raw = get(f"https://poisk-pro.ru/orders/russia/rabota-dlya-proektirovshchikov?page={page}")
        except Exception:
            break
        # карточка: ссылка+заголовок, затем short-text-<id> с описанием и бюджетом, затем дата
        cards = re.findall(
            r'<a href="(/order/(\d+)/[^"]+)">([^<]{8,120})</a>.*?'
            r'<div class="short-text-\2">(.*?)</div>.*?'
            r'(\d{2}\.\d{2}\.\d{4})',
            raw, re.S)
        seen = set()
        for u, oid, title, desc, date in cards:
            if oid in seen or title.strip() == 'Откликнуться': continue
            seen.add(oid)
            desc_c = html.unescape(re.sub(r'<[^>]+>', ' ', desc))
            desc_c = ' '.join(desc_c.split())
            text = f"{title.strip()} | {desc_c[:220]} | {date}"
            out.append({"source": f"poisk-pro/{niche_key}", "time": "",
                        "text": text[:400],
                        "url": "https://poisk-pro.ru" + u})
        if not cards: break
    return out

def fetch_se_guru(forum="optimizators-exchange", niche_key="seo"):
    """searchengines.guru — биржа SEO/контекст/таргет: темы-заказы, фильтр по дате автора (фактчек 09-11)."""
    out = []
    try:
        raw = get(f"https://searchengines.guru/ru/forum/{forum}")
    except Exception:
        return out
    blocks = re.findall(r'<div class="topics-list__title">\s*<a[^>]*href="(/ru/forum/\d+)"[^>]*>(.*?)</a>.*?'
                        r'<a href="/ru/users/\d+" title="(\d{4})\.(\d{2})\.(\d{2})', raw, re.S)
    seen = set()
    for u, txt, y, m, d in blocks:
        if u in seen: continue
        seen.add(u)
        clean = html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', txt))).strip()
        if not clean or len(clean) < 10: continue
        out.append({"source": f"se_guru/{niche_key}", "time": f"{y}-{m}-{d}",
                    "text": clean[:130],
                    "url": "https://searchengines.guru" + u})
    return out

def fetch_illustrators(niche_key="illustration-3d"):
    """illustrators.ru/jobs — заказы иллюстраций с бюджетами, без логина (фактчек 09-11)."""
    out = []
    try:
        raw = get("https://illustrators.ru/jobs", timeout=25)
    except Exception:
        return out
    cards = re.findall(r'<a class="job-card__link" href="(/jobs/[^"]+)">\s*'
                       r'<div class="job-card__title">([^<]+)</div>\s*'
                       r'(?:<div class="job-card__description">(.*?)</div>)?\s*'
                       r'(?:<div class="job-card__cost">\s*(?:<span[^>]*>([^<]*)</span>)?\s*([^<]*)</div>)?', raw, re.S)
    for u, title, desc, cost1, cost2 in cards[:40]:
        cost = (cost1 or "").strip() + " " + (cost2 or "").strip()
        text = html.unescape(re.sub(r'<[^>]+>', ' ', title)).strip()
        if desc:
            text += " | " + html.unescape(re.sub(r'<[^>]+>', ' ', desc)).strip()[:180]
        if cost.strip():
            text += " | " + ' '.join(cost.split())
        out.append({"source": f"illustrators/{niche_key}", "time": "",
                    "text": text[:250],
                    "url": "https://illustrators.ru" + u})
    return out

def fetch_tg(channels, niche_key):
    out = []
    for ch in channels:
        try:
            raw = get(f"https://t.me/s/{ch}")
        except Exception:
            continue
        posts = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', raw, flags=re.S)
        times = re.findall(r'<time datetime="([^"]+)"', raw)
        for p, t in zip(posts, times):
            txt = html.unescape(re.sub(r'<[^>]+>', '', re.sub(r'<br/?>', chr(10), p))).strip()
            out.append({"source": f"tg/{niche_key}", "time": t, "text": txt,
                        "url": f"https://t.me/s/{ch}"})
    return out

def fetch_avito(queries, niche_key):
    """Авито через Camoufox — market-signal (оферты исполнителей): разведка цен.
    Требует: pip install camoufox && python -m camoufox fetch (см. avito/README.md)."""
    import subprocess
    script = os.path.join(HERE, "avito", "avito_wlp.py")
    out_file = os.path.join(HERE, "avito", "avito_wlp_results.json")
    if not os.path.exists(script):
        return []
    try:
        subprocess.run([sys.executable, script] + queries[:2], timeout=240, check=False, capture_output=True)
        data = json.load(open(out_file, encoding="utf-8"))
        return [{"source": f"avito/{niche_key}", "time": "",
                 "text": d.get("title", "") + (" | " + str(d["price"]) if d.get("price") else ""),
                 "url": d.get("url", "")} for d in data]
    except Exception:
        return []



def fetch_kadrof(pages=3, niche_key="mix"):
    """Kadrof.ru — свежие заказы, без логина. Раздел: /work.
    Карточки: div.post.project-list с заголовком h6>a, бюджетом и описанием."""
    out = []
    for page in range(1, pages + 1):
        try:
            raw = get(f"https://kadrof.ru/work?page={page}", timeout=25)
        except Exception:
            break
        seen = set()
        # разбиваем на карточки по маркеру списка проектов
        for card in re.split(r'<div class="post project-list">', raw)[1:]:
            mt = re.search(r'href="(/work/\d+)"[^>]*>([^<]{5,140})<', card)
            if not mt:
                continue
            u, t = mt.group(1), html.unescape(mt.group(2)).strip()
            if u in seen:
                continue
            seen.add(u)
            mb = re.search(r'class="(?:fix-)?budget"[^>]*>(?:<span[^>]*>)?([^<]{0,40})', card)
            md = re.search(r'<div class="project-info">', card)
            body = re.sub(r'<[^>]+>', ' ', card[:md.start()] if md else card)
            body = html.unescape(re.sub(r'\s+', ' ', body)).strip()
            body = body.replace(t, '', 1).strip()
            parts = [t]
            if mb:
                b = html.unescape(re.sub(r'\s+', ' ', mb.group(1))).strip()
                if b and b not in ("—", "-", "0"):
                    parts.append(b)
            if body:
                parts.append(body[:400])
            text = " | ".join(p for p in parts if p)
            if len(text) < 20:
                continue
            out.append({"source": f"kadrof/{niche_key}", "time": "", "text": text[:600],
                        "url": "https://kadrof.ru" + u})
        if not out:
            break
    return out

# ---------------------------------------------------------------- скоринг
KW_CACHE = {}

# маркеры наёма персонала: 2+ = вакансия, не заказ (даже при точном попадании в нишу).
# «требуется/нужен» в списке НЕТ сознательно: на биржах заказов это стиль клиента
# («Требуется дизайнер для айдентики» — заказ). Наём выдают связки: ищем + в команду,
# вилка «от N ₽», з/п, оформление.
HR_MARKS = ("#вакансия", "в команду", "в штат", "з/п", "зарплат", "оклад",
            "собеседовани", "оформление по", "тк рф", "график работ", "ищем",
            "оплату обсуждаем при собеседовании")
# HR-структура вакансии — у заказа нет «Задачи/Требования/Условия» списком.
# Формат без двоеточия («Задачи 10-15 статей») тоже ловим: слово + цифры/перенос.
HR_STRUCT = ("задачи", "требования", "условия", "что мы предлагаем", "опыт работы")
# «ищет/ищу команду/в поисках» — вариации «ищем»
HR_SOFT = ("ищет ", "ищу ", "в поисках", "открыта вакансия", "на проектную работу")

# зарплатная вилка: «от 150 000 ₽/руб/руб./рублей/р.», диапазоны «от X до Y».
# Покрываем все формы записи суммы, включая сокращение «р.».
SALARY_RE = re.compile(
    r"(?:от\s*)?(\d[\d\s\u00a0.,]{2,12}?)\s*(?:до\s*[\d\s\u00a0.,]{2,12}?\s*)?(?:₽|руб\.?|рублей|р\.)"
)
# словесные маркеры бюджета ЗАКАЗА: при них сумма — это деньги проекта, не вилка
BUDGET_WORDS = ("бюджет", "оплата", "стоимост", "за проект", "смета")
VACANCY_WORD_RE = re.compile(r"(?<![а-яё#])ваканси[яию]", re.IGNORECASE)

def is_vacancy(txt):
    if "#вакансия" in txt: return True   # джоб-борд сам себя обозначил
    if VACANCY_WORD_RE.search(txt): return True   # «вакансия: ...» без хештега
    n = sum(1 for m in HR_MARKS if m in txt)
    n += sum(1 for m in HR_STRUCT if m in txt)   # v2.4: скелет вакансии
    n += sum(1 for m in HR_SOFT if m in txt)     # v2.4: вариации «ищем»
    # зарплатная вилка: сумма ≥ 60 000 без слов «бюджет/оплата» — месячная з/п, не
    # деньги проекта («бюджет 80 000 р.» в заказе — не наём). «От X до Y» тоже вилка.
    if SALARY_RE.search(txt):
        m = SALARY_RE.search(txt)
        try: amount = int(re.sub(r"[\s\u00a0.,]", "", m.group(1)))
        except ValueError: amount = 0
        is_project_budget = any(w in txt for w in BUDGET_WORDS)
        if amount >= 60000 and not is_project_budget:
            n += 2
    return n >= 2

_PREFIX = r"(?:с|со|по|за|на|от|до|пере|про|вы|при|у|из|раз|об|под|над|пред|без|не)?"

def _stem(k):
    return re.sub(
        r"(иями|ами|ями|ание|ания|аний|ение|ения|ений|ация|ации|ность|ство|"
        r"инг|ист|изм|ац|яц|иц|ер|ор|ар|ир|ов|ев|ий|ый|ой|ая|ые|"
        r"ing|ers|er|s|а|я|ы|и|е|у|о|ь)$", "", k)

def kw_search(txt, kws):
    """Совпадение ключей ниши с морфологией (та же логика, что в integrations/sources.py).

    - длинный ключ (5+) — по корню с приставками: «парсер» ловит «спарсить», «парсинг»;
    - короткий (3–4) — по границе слова с окончаниями: «бот» ловит «бота», но не «работа»;
    - совсем короткий (<=2) или с цифрой — точное слово: «ии» не ловится в «функции»;
    - ё/е и латиница/кириллица нормализуются (telegram↔телеграм).
    """
    norm = txt.replace("ё", "е")
    for lat, cyr in (("telegram", "телеграм"), ("python", "питон"),
                     ("wordpress", "вордпресс"), ("whatsapp", "ватсап")):
        if lat in norm or cyr in norm:
            norm += " " + lat + " " + cyr
    hits, seen = [], set()
    for k in (kws or []):
        k = k.strip().lower().replace("ё", "е")
        if not k:
            continue
        found = False
        if len(k) <= 2 or any(c.isdigit() for c in k):
            found = bool(re.search(r"(?<![а-яёa-z0-9])" + re.escape(k) + r"(?![а-яёa-z0-9])", norm))
        elif len(k) <= 4:
            st = _stem(k)
            found = bool(re.search(r"(?<![а-яёa-z0-9])" + re.escape(st) + r"[а-яё]{0,3}(?![а-яёa-z0-9])", norm))
            if not found and k.isascii():
                found = bool(re.search(r"[a-z]" + re.escape(k), norm))
        else:
            st = _stem(k)
            found = bool(re.search(r"(?<![а-яёa-z0-9])" + _PREFIX + re.escape(st), norm))
        if found:
            root = _stem(k)
            if root not in seen:
                seen.add(root)
                hits.append(k)
    return hits

def build_kws(niche):
    """Ключевые слова ниши = имя + queries. strong = точные формулировки ниши."""
    kws = {"strong": [], "medium": []}
    name = niche["name"].lower()
    strong_src = [name] + [q.lower() for q in niche.get("queries", [])]
    # срезаем очевидные стоп-слова из формулировок
    stop = ("и ", " и", " на", " на ", " для", " с ", " под ", "в ")
    kws["strong"] = list({s.strip() for s in strong_src if 3 < len(s.strip()) < 30})
    # medium — отдельные слова из имени и queries
    words = set()
    for s in strong_src:
        for w in re.findall(r'[а-яa-z]{4,}', s.lower()):
            words.add(w)
    common_ru = {"данные", "системы", "бизнеса", "бизнес", "сайта", "сайт", "услуги", "заказ", "менеджер"}
    kws["medium"] = list(words - common_ru)
    return kws

def score_post(p, kws):
    txt = re.sub(r'\s+', ' ', p.get("text", "").lower().replace('ё', 'е'))
    if len(txt) < 30: return 0, "коротко"
    if re.search(r'#резюме|#resume|ищу работу|в поиске работы|резюме [a-z@]', txt): return 0, "резюме (не заказ)"
    if is_vacancy(txt): return 0, "вакансия (наём, не заказ)"
    strong, med = kw_search(txt, kws["strong"]), kw_search(txt, kws["medium"])
    s, why = 0, []
    if strong: s += 40; why.append(f"точное: {', '.join(strong[:3])}")
    elif len(med) >= 2: s += 30; why.append(f"ниша по деталям: {', '.join(med[:3])}")
    elif med: s += 15; why.append(f"смежное: {', '.join(med[:3])}")
    else: return 0, "не ниша"
    for cat, keys, pts in (("urgent", ["срочно", "сегодня", "завтра", "asap", "горящ"], 15),
                           ("money", ["бюджет", "оплата", "₽", "руб", "вилка", "стоимост"], 25),
                           ("scope", ["тз есть", "детали в лс", "в лс", "примеры работ"], 10)):
        h = [k for k in keys if k in txt]
        if h: s += pts; why.append(h[0])
    m = re.search(r'(\d[\d\s]{2,9})\s*(?:₽|руб|тыс)', txt)
    if m:
        val = int(re.sub(r'\s', '', m.group(1)))
        if 1000 <= val <= 600000: s += 10; why.append(f"бюджет ~{val}₽")
    if p.get("time"):
        try:
            age_h = (datetime.now(MSK) - datetime.fromisoformat(p["time"])).total_seconds() / 3600
            if age_h <= 24: s += 10; why.append("<24ч")
            elif age_h <= 72: s += 5
            elif age_h > 400: s -= 15; why.append(f"{int(age_h/24)}дн")
        except Exception: pass
    return max(0, min(100, s)), "; ".join(why)

# ---------------------------------------------------------------- run
def run_niche(key, niche, full=False, enrich_top=0):
    t0 = time.time()
    posts = []
    src_ok, src_err = [], []   # v2.4: живость источников — бенчмарк скорости привязан к ним
    def _src(fn, tag, *a):
        try:
            added = fn(*a)
            n = len(posts)
            posts.extend(added)
            src_ok.append(f"{tag}+{len(posts)-n}")
        except Exception as e:
            src_err.append(f"{tag} ERR {str(e)[:40]}")
    def _last(tag):
        """Результат конкретного источника: 'fl/rss+60' не должен подменять 'fl'."""
        for x in reversed(src_ok + src_err):
            if x.startswith(tag + "+") or x.startswith(tag + " "):
                return x
        return ""
    if niche.get("fl") or True:
        # v2.7: FL.ru берём из официальной RSS-ленты (60 заказов с текстами одним запросом),
        # а HTML-категорию — как добор. Прежний HTML-сборщик падал в таймаут почти в каждом прогоне.
        _src(fetch_fl_rss, "fl/rss", key)
        print(f"  [fl/rss] {_last('fl/rss')}")
    if niche.get("fl"):
        _src(fetch_fl, "fl", niche["fl"], key)
        print(f"  [fl] {_last('fl')}")
    if niche.get("wl"):
        _src(fetch_weblancer, "wl", niche["wl"], key)
        print(f"  [wl] {src_ok[-1] if src_ok and src_ok[-1].startswith('wl') else src_err[-1] if src_err and src_err[-1].startswith('wl') else ''}")
    if niche.get("kw") is not None:
        _src(fetch_kwork, "kw", niche["kw"], key)
        print(f"  [kw] {src_ok[-1] if src_ok and src_ok[-1].startswith('kw') else src_err[-1] if src_err and src_err[-1].startswith('kw') else ''}")
    if niche.get("tg"):
        _src(fetch_tg, "tg", niche["tg"], key)
        print(f"  [tg] {src_ok[-1] if src_ok and src_ok[-1].startswith('tg') else src_err[-1] if src_err and src_err[-1].startswith('tg') else ''}")
    if "kadrof" in (niche.get("sources_extra") or []):
        _src(fetch_kadrof, "kadrof", 2, key)
        print(f"  [kadrof] {src_ok[-1] if src_ok and src_ok[-1].startswith('kadrof') else src_err[-1] if src_err and src_err[-1].startswith('kadrof') else ''}")
    extras = niche.get("sources_extra") or []
    if "poisk-pro" in extras:
        _src(fetch_poisk_pro, "poisk-pro", key)
        print(f"  [poisk-pro] {src_ok[-1] if src_ok and src_ok[-1].startswith('poisk-pro') else src_err[-1] if src_err and src_err[-1].startswith('poisk-pro') else ''}")
    if "se-guru" in extras:
        _src(fetch_se_guru, "se-guru", key)
        print(f"  [se-guru] {src_ok[-1] if src_ok and src_ok[-1].startswith('se-guru') else src_err[-1] if src_err and src_err[-1].startswith('se-guru') else ''}")
    if "illustrators" in extras:
        _src(fetch_illustrators, "illustrators", key)
        print(f"  [illustrators] {src_ok[-1] if src_ok and src_ok[-1].startswith('illustrators') else src_err[-1] if src_err and src_err[-1].startswith('illustrators') else ''}")
    if key == "freelance-mix":
        _src(fetch_kadrof, "kadrof", 4, key)
        print(f"  [kadrof] {src_ok[-1] if src_ok and src_ok[-1].startswith('kadrof') else src_err[-1] if src_err and src_err[-1].startswith('kadrof') else ''}")
    if full and niche.get("queries"):
        _src(fetch_avito, "avito", niche["queries"], key)
        print(f"  [avito] {src_ok[-1] if src_ok and src_ok[-1].startswith('avito') else src_err[-1] if src_err and src_err[-1].startswith('avito') else ''}")
    kws = build_kws(niche)
    # v2.7: один заказ часто висит на бирже, в RSS и в трёх Telegram-каналах.
    # Схлопываем до одного лида, сохраняя ссылки на остальные площадки.
    posts, merged = dedup_posts(posts)
    scored = []
    for p in posts:
        sc, why = score_post(p, kws)
        if sc > 0: scored.append({**p, "score": sc, "why": why})
    scored.sort(key=lambda x: -x["score"])
    leads = [p for p in scored if not p["source"].startswith("avito/")]
    market = [p for p in scored if p["source"].startswith("avito/")]
    hot = [p for p in leads if p["score"] >= 60]
    warm = [p for p in leads if 40 <= p["score"] < 60]
    # Обогащение топ-тёплых по открытым госисточникам: из текста объявления достаём
    # ИНН/название/домен и добираем юрлицо, отрасль, живость, руководителя, email.
    # Медленный шаг (ЕГРЮЛ + МСП + «Работа России»), поэтому только по явному флагу
    # и только по верхушке — обогащать весь поток бессмысленно и грубо к госсервисам.
    enriched = []
    if enrich_top and _enrich:
        # Если «горячих» меньше, чем просили обогатить, добираем из тёплых: иначе на
        # реальном потоке (🔥 часто 0) флаг молча не делает ничего.
        pool = hot[:enrich_top] if len(hot) >= enrich_top else (hot + warm)[:enrich_top]
        for p in pool:
            try:
                card = _enrich.enrich(text=p.get("text", ""))
                enriched.append({"url": p.get("url"), "score": p["score"], "card": card})
                state = "🟢 найден" if card.get("found") else "⚪ не найден"
                print(f"  [enrich] {state} {p.get('score')} · {(p.get('url') or '')[:56]}")
            except Exception as e:
                print(f"  [enrich] ERR {str(e)[:60]}")
            time.sleep(1.2)  # пауза: госсервисы роняют серии запросов
    dt = time.time() - t0
    # v2.4: метрики отчёта — сколько 🔥 назвали деньги (главный предиктор «тёплоты»)
    # и сколько отсеяно как вакансии/резюме (HR-фильтр)
    money_keys = ("₽", "руб", "бюджет", "оплата", "стоимост", "тыс")
    hot_with_money = sum(1 for p in hot if any(k in p["text"].lower() for k in money_keys))
    src_line = f"источники {len(src_ok)} ок / {len(src_err)} таймаут" + (f" ({', '.join(src_err)})" if src_err else "")
    print(f"  → {niche['name']}: {len(posts)} постов → {len(leads)} в нише → 🔥{len(hot)} 🌤{len(warm)} | "
          f"🔥 с деньгами {hot_with_money}/{len(hot)} | схлопнуто дублей {merged} | {src_line} | {dt:.0f}с")
    return {"key": key, "name": niche["name"], "total": len(posts), "leads": len(leads),
            "hot": len(hot), "warm": len(warm), "market": len(market), "sec": round(dt),
            "merged": merged,
            "src_ok": len(src_ok), "src_err": len(src_err), "src_err_names": list(src_err),
            "hot_money": hot_with_money,
            "enriched": enriched,
            "hot_items": [{k: p[k] for k in ("score", "source", "text", "url", "why")} for p in hot[:5]],
            "warm_items": [{k: p[k] for k in ("score", "source", "text", "url", "why")} for p in warm[:5]]}

def render_report(results):
    ts = datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
    rep = [f"# Warm Lead Report — мульти-ниша — {ts} (MSK)", ""]
    tot = sum(r["total"] for r in results); led = sum(r["leads"] for r in results)
    hot = sum(r["hot"] for r in results); mkt = sum(r["market"] for r in results)
    sec = max(r["sec"] for r in results)
    hot_money = sum(r.get("hot_money", 0) for r in results)
    rep.append(f"Ниш: {len(results)} | Собрано: {tot} | В нише: {led} | 🔥 {hot} | 🔥 с деньгами {hot_money}/{hot} | время {sec}с")
    errs = [e for r in results for e in r.get("src_err_names", [])]
    if errs:
        rep.append(f"Источники с таймаутами: {len(errs)} — {'; '.join(errs[:8])}")
    # экономика прогона: разбор отчёта + отклик каждому тёплому (см. docs/ru/economics.md)
    rate = int(os.environ.get("WLP_HOURLY", "1500"))
    my_min = 20 + 15 * hot
    cost = my_min / 60 * rate
    cpl = (cost / hot) if hot else None
    rep.append(f"Экономика: ~{my_min} мин работы ({cost:.0f}₽ при ставке {rate}₽/ч; своя ставка — WLP_HOURLY) · "
               f"цена тёплого лида ≈ " + (f"{cpl:.0f}₽" if cpl else "∞ (0 тёплых)"))
    rep.append("")
    rep.append("| Ниша | Всего | В нише | 🔥 | 🌤 | 🔥 с деньгами | Источники | сек |")
    rep.append("|---|---|---|---|---|---|---|---|")
    for r in sorted(results, key=lambda x: -(x["hot"] * 10 + x["warm"])):
        src_col = f"{r.get('src_ok', 0)} ок / {r.get('src_err', 0)} таймаут"
        rep.append(f"| {r['name']} | {r['total']} | {r['leads']} | {r['hot']} | {r['warm']} | {r.get('hot_money', 0)}/{r['hot']} | {src_col} | {r['sec']} |")
    rep.append("")
    for r in results:
        if r["hot_items"]:
            rep.append(f"## 🔥 {r['name']}")
            for p in r["hot_items"]:
                rep.append(f"- **[{p['score']}]** {p['source']} | {p['text'][:100]}")
                rep.append(f"  - {p['url']}")
                rep.append(f"  - {p['why']}")
            rep.append("")
    # Кто стоит за объявлением: проверка по открытым госисточникам (флаг --enrich).
    # Именно этот блок отвечает на вопрос «живая это компания или однодневка» —
    # данные берутся из госреестров, а не из обещаний самого заказчика.
    if any(r.get("enriched") for r in results):
        rep.append("## 🏢 Кто за объявлением — проверка по госисточникам")
        rep.append("")
        rep.append("_ЕГРЮЛ · реестр МСП · «Работа России». Без ключей, регистраций и оплаты._")
        rep.append("")
        for r in results:
            if not r.get("enriched"):
                continue
            rep.append(f"### {r['name']}")
            for e in r["enriched"]:
                rep.append(_enrich.format_card(e["card"]))
                if e.get("url"):
                    rep.append(f"  - объявление: {e['url']}")
                rep.append("")
    return "\n".join(rep)

def main():
    niches = load_niches()
    args = sys.argv[1:]
    if "--list" in args:
        for i, (k, v) in enumerate(niches.items(), 1):
            print(f"{i:2}. {k:30} {v['name']}")
        return
    full = "--full" in args
    # --enrich [N]: обогатить топ-N тёплых лидов по госисточникам (по умолчанию 3).
    # Шаг медленный и стучится в госсервисы — включать осознанно, не по умолчанию.
    enrich_top = 0
    for a in args:
        if a == "--enrich":
            enrich_top = 3
        elif a.startswith("--enrich-top="):
            try:
                enrich_top = max(0, int(a.split("=", 1)[1]))
            except ValueError:
                pass
    args = [a for a in args if not a.startswith("--")]
    keys = [args[0]] if args and args[0] in niches else list(niches.keys())
    if args and args[0] not in niches:
        print(f"!! ниша '{args[0]}' не найдена (--list для списка)"); return
    if enrich_top and not _enrich:
        print("!! integrations/enrich.py не найден — обогащение выключено"); enrich_top = 0
    results = []
    print(f"== Warm Lead Parser v2.7: {len(keys)} ниш, режим {'полный' if full else 'быстрый'}"
          + (f", обогащение топ-{enrich_top}" if enrich_top else "") + " ==")
    for k in keys:
        print(f"\n— {niches[k]['name']} —")
        results.append(run_niche(k, niches[k], full=full, enrich_top=enrich_top))
    report = render_report(results)
    fn = os.path.join(HERE, f"warm-lead-report-{datetime.now(MSK).strftime('%Y-%m-%d_%H%M')}.md")
    open(fn, "w", encoding="utf-8").write(report)
    print(f"\n→ Отчёт: {fn}")

if __name__ == "__main__":
    main()
