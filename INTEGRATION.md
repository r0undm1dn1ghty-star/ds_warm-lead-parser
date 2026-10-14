# Универсальная установка — за 1 минуту, без зависимостей

Этот скилл можно подключить к **любой** агентной системе или чат-боту. Три способа,
выбирай по своему стеку. Установка = скопировать папку. Никаких pip, npm, API-ключей.

```
integrations/
  sources.py      ← чистые функции скоринга (stdlib-only, без сети)
  mcp_server.py   ← MCP-сервер (4 инструмента: score_lead, classify_batch, explain_rules, selftest)
  SKILL.md        ← сам скилл
```

Проверка правил без сети: `python integrations/sources.py --selftest` (9/9 кейсов).
Скоринг батча из CLI: `echo "нужен парсер, бюджет 50 тыс." | python integrations/sources.py --json`

---

## Способ 1 — HTTP (универсальный: боты, n8n, Make, Zapier, Bitrix24, любой стек)

Подходит всем, кто умеет делать HTTP-запрос. Запусти сервис рядом с ботом:

```bash
python integrations/skillkit.py --serve --port 8765
```

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/run` | `{"input": ...}` → результат скилла (JSON) |
| GET | `/health` | статус сервиса |
| GET | `/openapi.json` | OpenAPI 3.1 — для GPT Actions, Swagger UI, генерации клиентов |
| GET | `/prompt` | системный промпт для LLM-агента без доступа к репо |

Примеры клиентов:

```bash
# cURL
curl -s localhost:8765/run -H 'Content-Type: application/json' \
  -d '{"input": {"text": "..."}}' | python -m json.tool

# Python (stdlib, без requests)
python - <<'PY'
import json, urllib.request
req = urllib.request.Request("http://localhost:8765/run",
    data=json.dumps({"input": {"text": "..."}}).encode(),
    headers={"Content-Type": "application/json"})
print(json.loads(urllib.request.urlopen(req).read()))
PY
```

```python
# Telegram-бот (telebot/aiogram) — 3 строки
import json, urllib.request
def ask(text):
    req = urllib.request.Request("http://localhost:8765/run",
        data=json.dumps({"input": text}).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["result"]
```

```js
// Node.js / любой JS-бот
const r = await fetch("http://localhost:8765/run", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({input: {text: "..."}})
});
const {result} = await r.json();
```

```yaml
# n8n / Make: HTTP Request node
Method: POST
URL: http://localhost:8765/run
Body (JSON): {"input": {"text": "{{ $json.message }}"}}
```

## Способ 2 — CLI (пайплайны, cron, bash-боты, SSG)

```bash
python integrations/skillkit.py --json --input '{"text": "..."}'
echo '{"text": "..."}' | python integrations/skillkit.py --json
python integrations/skillkit.py --prompt     # системный промпт для LLM
python integrations/skillkit.py --openapi    # OpenAPI 3.1
```

## Способ 3 — импорт как библиотека (Python-агенты)

```python
import sys; sys.path.insert(0, "integrations")
import skillkit
result = skillkit.run({"text": "..."})     # чистый dict
```
Совместимо с LangChain / LlamaIndex / CrewAI / AutoGen как обычная Python-функция.

---

## MCP (Claude Desktop, Claude Code, Cursor, Cline, Continue, свой MCP-клиент)

Если у репозитория есть `integrations/mcp_server.py` — подключение через конфиг:

```json
{
  "mcpServers": {
    "ds_warm-lead-parser": { "command": "python", "args": ["/абсолютный/путь/integrations/mcp_server.py"] }
  }
}
```

---

## GPT Actions / ассистенты с OpenAPI

1. Запусти `python integrations/skillkit.py --serve --port 8765`
2. Опубликуй туннелем (ngrok/cloudflared) и укажи в Action URL `/openapi.json`
   (или импортируй схему из `--openapi`).

---

## Агенты, читающие файлы (Claude Projects, ChatGPT с файлами, свой RAG)

1. Загрузи `SKILL.md` (+ `docs/`) как инструкцию/знание.
2. Прогони `llms-full.txt` как контекст-карту.
3. Опционально: дай `--prompt` как системный промпт.

---

## Проверка установки

```bash
python integrations/skillkit.py --input '{"text": "тест"}'   # должен вернуть JSON
python integrations/mcp_server.py < /dev/null                # MCP не падает на пустом вводе
```

## Границы

- Только стандартная библиотека Python 3.8+. **Никаких pip install, никаких API-ключей.**
- HTTP-сервис слушает `127.0.0.1` по умолчанию — наружу не выставлен.
- Скилл не собирает персональные данные и не рассылает сообщения (см. SAFETY_RAILS/SKILL.md).
