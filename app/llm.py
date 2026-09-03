# -*- coding: utf-8 -*-
"""Клиент к OpenAI-совместимому API ai-public.

Ключ живёт только на сервере: читается из файла и никогда не отдаётся в браузер.
Именно этим проект отличается от референса, где статика уходила в nginx,
а ключ был виден в исходниках страницы.
"""
import json
import os
import pathlib
import time
import urllib.error
import urllib.request

# Провайдер и модель взяты из ~/.config/opencode/opencode.jsonc (провайдер ai-public)
BASE_URL = "https://ai-public.a101.ru/api"
MODEL = "deepseek/deepseek-v4-pro"

# Путь к ключу можно переопределить переменной окружения
KEY_FILE = pathlib.Path(
    os.getenv("AI_PUBLIC_KEY_FILE") or pathlib.Path.home() / ".secrets" / "ai-public"
)

# Параметры сэмплинга одинаковы для всех режимов и намеренно не выведены в интерфейс:
# разница в ответах должна идти от system prompt, а не от настроек генерации.
TEMPERATURE = 0.2

# deepseek-v4-pro — рассуждающая модель: max_tokens это общий бюджет на
# reasoning_content и на content. На этом эндпоинте content не голодает даже при
# max_tokens=300 (проверено), но запас берём щедрый — «Совет экспертов» на
# сложных задачах легко съедает несколько тысяч токенов.
MAX_TOKENS = 32000

TIMEOUT_SEC = 300


class LlmError(RuntimeError):
    """Ошибка вызова API, пригодная для показа в интерфейсе."""


_key_cache = None


def read_api_key():
    """Ключ: файл -> переменная окружения. Кэшируется на процесс."""
    global _key_cache
    if _key_cache:
        return _key_cache

    if KEY_FILE.is_file():
        # utf-8-sig снимает BOM, если файл сохранён Блокнотом
        key = KEY_FILE.read_text(encoding="utf-8-sig").strip()
        if key:
            _key_cache = key
            return key

    key = (os.getenv("AI_PUBLIC_API_KEY") or "").strip()
    if key:
        _key_cache = key
        return key

    raise LlmError(
        f"API-ключ не найден. Положите его в {KEY_FILE} "
        "или задайте переменную окружения AI_PUBLIC_API_KEY."
    )


def complete(messages, system=None):
    """Один запрос к модели.

    system опционален: если его нет, system-сообщение в запрос не уходит вовсе.
    Возвращает dict с text, finish_reason, usage, latency_ms и reasoning_tokens.
    """
    payload = []
    if system:
        payload.append({"role": "system", "content": system})
    payload.extend({"role": m["role"], "content": m["content"]} for m in messages)

    body = json.dumps(
        {
            "model": MODEL,
            "messages": payload,
            "stream": False,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {read_api_key()}",
            "Content-Type": "application/json",
        },
    )

    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise LlmError(f"API вернул {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LlmError(f"Сеть недоступна: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise LlmError(f"API вернул не JSON: {e}") from e

    try:
        choice = data["choices"][0]
    except (KeyError, IndexError) as e:
        raise LlmError(f"Неожиданный формат ответа API: {json.dumps(data)[:300]}") from e

    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}

    return {
        "text": choice.get("message", {}).get("content") or "",
        "finish_reason": choice.get("finish_reason"),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "reasoning_tokens": details.get("reasoning_tokens", 0),
        },
        "latency_ms": round((time.time() - started) * 1000),
    }


def sum_usage(results):
    """Складывает usage нескольких вызовов — у многошаговых режимов их больше одного."""
    total = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
    }
    for result in results:
        for field in total:
            total[field] += result.get("usage", {}).get(field, 0)
    return total
