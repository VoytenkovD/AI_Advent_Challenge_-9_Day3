# -*- coding: utf-8 -*-
"""Локальный веб-сервер: статика + прокси к LLM.

Только стандартная библиотека Python — ни node, ни npm, ни docker не нужны.
Ключ API остаётся на сервере и в браузер не попадает.

Запуск:  python app/server.py  [--port 5175] [--no-browser]
"""
import argparse
import json
import mimetypes
import os
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import MAX_TOKENS, MODEL, LlmError, read_api_key  # noqa: E402
from modes import MODE_KEYS, mode_catalog, run_mode  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_QUESTION_LEN = 8000
MAX_BODY_BYTES = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "ThoughtForge/1.0"

    # --- утилиты ответа -------------------------------------------------

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Приложение локальное и учебное, но кэш статики только мешает разработке
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass  # пользователь закрыл вкладку посреди запроса

    def _send_json(self, code, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    # --- GET: статика и метаданные --------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/api/config":
            self._send_json(
                200,
                {
                    "model": MODEL,
                    "maxTokens": MAX_TOKENS,
                    "modes": mode_catalog(),
                },
            )
            return

        if path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html")
            return

        # Защита от выхода за пределы web/ (path traversal)
        candidate = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(candidate).startswith(str(STATIC_DIR.resolve())):
            self._send_json(403, {"error": "Доступ запрещён"})
            return

        self._serve_file(candidate)

    def _serve_file(self, file_path):
        if not file_path.is_file():
            self._send_json(404, {"error": f"Не найдено: {file_path.name}"})
            return
        content_type, _ = mimetypes.guess_type(str(file_path))
        if content_type in ("text/html", "text/css", "application/javascript", "text/javascript"):
            content_type += "; charset=utf-8"
        self._send(200, file_path.read_bytes(), content_type or "application/octet-stream")

    # --- POST: запуск режимов -------------------------------------------

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/run":
            self._send_json(404, {"error": "Неизвестный endpoint"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json(400, {"error": "Некорректный Content-Length"})
            return

        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"error": "Пустое или слишком большое тело запроса"})
            return

        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": f"Некорректный JSON: {e}"})
            return

        question = (request.get("question") or "").strip()
        if not question:
            self._send_json(400, {"error": "Вопрос пуст"})
            return
        if len(question) > MAX_QUESTION_LEN:
            self._send_json(
                400, {"error": f"Вопрос длиннее {MAX_QUESTION_LEN} символов"}
            )
            return

        requested = request.get("modes") or MODE_KEYS
        modes = [m for m in requested if m in MODE_KEYS]
        if not modes:
            self._send_json(400, {"error": "Не указано ни одного известного режима"})
            return

        # Все четыре режима идут параллельно: суммарно до 8 запросов к API,
        # последовательно это было бы втрое дольше.
        results = {}
        with ThreadPoolExecutor(max_workers=len(modes)) as pool:
            futures = {pool.submit(run_mode, key, question): key for key in modes}
            for future, key in futures.items():
                try:
                    results[key] = {"status": "done", **future.result()}
                except LlmError as e:
                    results[key] = {"status": "error", "error": str(e)}
                except Exception as e:  # noqa: BLE001
                    results[key] = {
                        "status": "error",
                        "error": f"{type(e).__name__}: {e}",
                    }

        self._send_json(200, {"question": question, "results": results})

    def log_message(self, fmt, *args):
        # Тише стандартного логгера: только метод и путь
        sys.stderr.write(f"  {self.command} {self.path}\n")


def main():
    parser = argparse.ArgumentParser(description="Thought Forge — 4 способа рассуждения")
    parser.add_argument("--port", type=int, default=5175)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # Проверяем ключ до старта: иначе ошибка всплыла бы только на первом запросе
    try:
        key = read_api_key()
        print(f"Ключ загружен (...{key[-4:]}), модель: {MODEL}")
    except LlmError as e:
        print(f"ОШИБКА: {e}")
        return 1

    if not (STATIC_DIR / "index.html").is_file():
        print(f"ОШИБКА: не найден {STATIC_DIR / 'index.html'}")
        return 1

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        print(f"ОШИБКА: не удалось занять порт {args.port}: {e}")
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"Сервер запущен: {url}")
    print("Остановить: Ctrl+C")

    if not args.no_browser and not os.getenv("TF_NO_BROWSER"):
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
