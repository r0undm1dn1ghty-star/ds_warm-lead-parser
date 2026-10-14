# ds_warm-lead-parser

**Discovery Warm Lead Parser**

<div align="center">
<img src="assets/banner.svg" alt="Discovery System — Warm Lead Parser — скоринг 0–100, цена лида" width="100%">
</div>

<div align="center">

**Находит клиентов в открытых источниках и говорит, с кем связаться первым.**

`[00:20] 53 ниши · 57 телеграм-каналов + 7 бирж + Авито → 4400 постов → 590 в нише → 🔥 49 + 💰 цена лида`

![лицензия MIT](https://img.shields.io/badge/лицензия-MIT-blue)
![версия 2.4.0](https://img.shields.io/badge/версия-2.4.0-FFB224)
![python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)
![53 ниши](https://img.shields.io/badge/ниш-53-3DDC84)
![без логинов и спама](https://img.shields.io/badge/этика-без%20логинов%20и%20спама-4c6ef5)

</div>

> **Warm Lead Parser не спамит и не «парсит всё».** Читает только открытые публичные ленты, оценивает каждый пост по готовности платить и отдаёт короткий список: с кем связаться сегодня и почему. Каждый отчёт считает цену тёплого лида в ₽.

---

## Установка и использование

Скилл ставится копированием папки (или `SKILL.md`) в `.claude/skills/` / `.hermes/skills/` — и живёт под именем **`ds_warm-lead-parser`**. В чате агенту достаточно сказать: «запусти ds_warm-lead-parser для …» — имя уникально и не конфликтует с чужими скиллами.

```bash
# вариант 1 — клон репо целиком
git clone https://github.com/r0undm1dn1ghty-star/discovery-warm-lead-parser.git && cp -r discovery-warm-lead-parser/.claude/skills/* ~/.claude/skills/ 2>/dev/null || cp discovery-warm-lead-parser/SKILL.md ~/.claude/skills/ds_warm-lead-parser/SKILL.md

# вариант 2 — только SKILL.md (у кого он есть в корне)
cp discovery-warm-lead-parser/SKILL.md ~/.claude/skills/ds_warm-lead-parser/SKILL.md
```

Все скиллы Discovery System — с префиксом **`ds_`**: `ds_pnl-check`, `ds_spiral-maturity`, `ds_geo-audit`, `ds_market-evidence`, `ds_warm-lead-parser`, `ds_content-radar`. Комплект и связка — [discovery-system.ru](https://discovery-system.ru).

## Зачем это бизнесу

Вы продаёте услуги или внедрения — а ваши будущие клиенты прямо сейчас пишут «нужен бот», «срочно доделать», «внедрить ИИ», «нарисовать арт», «нужен репетитор» на биржах и в чатах. Пока вы не видите эти посты — их разбирает кто-то другой, часто в тот же день.

Warm Lead Parser закрывает этот разрыв за один прогон — в **53 преднастроенных нишах**: от ИИ-агентов и чат-ботов до копирайтинга, дизайна, SEO, репетиторства и инженерных чертежей. Своя ниша добавляется в конфиг за минуту.

| Шаг | Что происходит | Результат |
|---|---|---|
| **1. Собрать** | Движок читает 64 источника: 57 телеграм-каналов (заказы/биржи), 7 веб-бирж (FL.ru, Weblancer, Kwork, Kadrof, searchengine.guru, illustrators.ru, poisk-pro) и Авито (опционально, стелс). Каждый источник проверяется на живость при прогоне: не ответил — пропускается, не роняя бюджет. | Тысячи постов с датами, ценами, ссылками. |
| **2. Оценить** | Каждый пост получает балл готовности 0–100: точная ли тема, названы ли деньги, есть ли срочность и ТЗ. Вакансии-наёмка и резюме соискателей отсеиваются. | Отсортированный список с объяснением каждого балла. |
| **3. Решить** | 🔥 связаться сегодня → 🌤 мягкий заход → 📊 рыночные сигналы Авито (цены конкурентов). | Приоритет + экономика: цена тёплого лида в ₽. |

## Быстрый старт (2 минуты)

1. Убедитесь, что установлен [Python 3.10+](https://www.python.org/downloads/) (стандартная библиотека, без зависимостей).
2. Скачайте [`warm_lead_parser.py`](warm_lead_parser.py) и [`niches-50.json`](niches-50.json) в одну папку.
3. Посмотрите список ниш и запустите — все или одну:
   ```bash
   python warm_lead_parser.py --list        # 53 ниши
   python warm_lead_parser.py               # все ниши
   python warm_lead_parser.py chatbots      # одна ниша, полный режим
   ```
4. Откройте `warm-lead-report-<дата>.md`: таблица ниш, тёплые лиды с ссылками и объяснением баллов, экономика прогона.

**Не хотите терминал?** Откройте `warm_lead_parser.py` в любом ИИ-агенте (Claude, ChatGPT, Gemini, локальный агент) и скажите: «разберись и запусти». Файл самодокументирован.

**Авито (опционально):** требуется стелс-браузер Camoufox — установка одной командой, детали в [`avito/README.md`](avito/README.md). Без него парсер работает на всех остальных источниках и честно пишет «пропускаю».

## Проверка на реальных рыночных данных

Скилл прогнан на реальных данных рынка — не на синтетике. Полный разбор с
цифрами, покрытием и воспроизводимыми командами: **[EVAL.md](EVAL.md)**.

Бенчмарк против наивного метода на реальных данных: **[BENCHMARK.md](BENCHMARK.md)**.

## Что внутри

| Путь | Содержимое |
|---|---|
| [`warm_lead_parser.py`](warm_lead_parser.py) | Движок: сбор + скоринг + экономика. Один файл, stdlib-only. |
| [`niches-50.json`](niches-50.json) | 53 ниши: ключи, источники, запросы. Правится текстом. |
| [`avito/`](avito/) | Авито-сборщик на Camoufox (опциональный источник). |
| [`examples/`](examples/) | рыночные прогоны на живых источниках: [`competition-2026-09-20.md`](examples/competition-2026-09-20.md). |
| [`assets/`](assets/) | Логотип и баннер (INDIGO TERMINAL). |
| [`reports/`](reports/) | Реальные отчёты прогонов. |
| [`docs/ru/`](docs/ru/) | Источники, скоринг, экономика, риски FL. |
| [`docs/en/`](docs/en/) | English summary. |
| [`SAFETY_RAILS.md`](SAFETY_RAILS.md) | Этика и границы. |
| [`CHANGELOG.md`](CHANGELOG.md), [`LICENSE`](LICENSE) | История, MIT. |

## Чего НЕ делает

| Не делает | Почему |
|---|---|
| Не собирает телефоны/email массово, не рассылает | Спам-инструментация, забанят домен; работаем с публичными постами «ищу исполнителя» |
| Не логинится, не решает капчи, не сканирует приватные группы | Легальный периметр: публичный поиск как руками |
| Не обещает «100500 лидов в час» | Честная скорость: ~35 секунд на нишу при живых источниках; потолок ожидания любого сокета — 12 секунд с версии 2.3 |
| Не заменяет продажи | Парсер — первая линия; продаёте вы |

## Скоринг: 0-100

| Сигнал | Баллы |
|---|---|
| Точное попадание в нишу (формулировки ниши из конфига) | +40 |
| Смежная тема (слова ниши) | +15 |
| Названы деньги (бюджет, оплата, ₽) | +25 |
| Срочность | +15 |
| Готов к контакту (ТЗ, «в ЛС») | +10 |
| Бюджет в коридоре 1К–600К ₽ | +10 |
| Свежесть <24ч / <72ч | +10 / +5 |
| Пост старше 17 дней | −15 |
| #резюме / «ищу работу» | 0 — соискатель, не клиент |

**🔥 60+** связаться сегодня · **🌤 40–59** мягкий заход · ниже — мимо.

## Roadmap

- [x] v1.0 — 6 площадок (FL.ru, Kwork, Freelance.ru, Weblancer, Telegram, Авито), скоринг 0–100, экономика
- [x] v1.1 — 100 источников: 76 ТГ-каналов (t.me/s/) + 23 веб-биржи + Авито-стелс; hiring-фильтр вакансий; красные флаги (rescue/lowball/tos-risk)
- [x] v2.0 — мульти-ниша: 53 ниши в конфиге, скоринг по ключам ниши, запуск всех ниш одним прогоном
- [x] v2.1 — третья волна фактчека: пул живых ТГ-каналов пересобран, +Kadrof (заказы с датами и бюджетами), фильтр #резюме (соискатели ≠ клиенты)
- [x] v2.2 — +3 специализированные биржи: poisk-pro.ru (инженерия/чертежи, заказы 20–233К ₽), searchengines.guru (SEO/контекст/таргет), illustrators.ru (графика 2D/3D)
- [x] v2.3 — жёсткий потолок ожидания сокета (`socket.setdefaulttimeout(12)`): один зависший источник (FL.ru) больше не сжирает бюджет прогона; исправлен бенчмарк скорости в README на честный «~35 секунд на нишу при живых источниках»
- [ ] v2.4 — автопилот: cron-прогон, дайджест в Telegram
- [ ] v2.5 — ИИ-агент пишет черновики откликов

## Проект Discovery System

Warm Lead Parser — бесплатный инструмент [Discovery System](https://r0undm1dn1ghty-star.github.io/discovery-system/): рыночная разведка в мире ИИ-агентов. Калькулятор стоимости рутины, гайд связок, разборы с источниками — на [лендинге](https://r0undm1dn1ghty-star.github.io/discovery-system/).

## Лицензия

MIT — свободно, с атрибуцией. Границы — в [`SAFETY_RAILS.md`](SAFETY_RAILS.md).

## English summary

**Discovery Warm Lead Parser** — open-source tool that finds buyers in public sources (57 Telegram job boards via t.me/s/, FL.ru, Weblancer, Kwork, Kadrof, poisk-pro.ru, searchengines.guru, illustrators.ru, Avito via stealth browser) and ranks them 0–100 by readiness-to-pay across 53 pre-configured niches. Economics in every run (cost per warm lead), no logins, no spam, resume-posts filtered out. One Python file, stdlib-only, works with any AI agent. Russian is authoritative — see [`docs/en/`](docs/en/).

---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "Discovery Warm Lead Parser",
  "applicationCategory": "BusinessApplication",
  "operatingSystem": "Windows, macOS, Linux",
  "description": "Open-source инструмент поиска тёплых лидов в открытых источниках (57 телеграм-каналов, FL.ru, Weblancer, Kwork, Kadrof, poisk-pro, searchengines.guru, illustrators.ru, Авито) со скорингом готовности 0-100 в 53 нишах и экономикой каждого прогона.",
  "offers": {"@type": "Offer", "price": "0", "priceCurrency": "RUB"},
  "author": {"@type": "Organization", "name": "Discovery System"},
  "programmingLanguage": "Python",
  "license": "https://opensource.org/licenses/MIT",
  "featureList": ["53 ниши из коробки", "Скоринг 0-100 с объяснением", "Экономика лида в каждом отчёте", "Без логинов и API-ключей", "Конфиг правится текстом"],
  "audience": {"@type": "Audience", "audienceType": "предприниматели, фрилансеры, агентства, ИИ-агенты"}
}
</script>

## English summary

Warm Lead Parser — open-source tool for finding warm leads from public sources: FL.ru, Kwork, Freelance.ru, Weblancer, Telegram boards, Avito. Scoring 0-100, run economics (cost per lead). Battle-tested: 4407 posts parsed, 49 hot leads, 385 rub per lead. MIT.
