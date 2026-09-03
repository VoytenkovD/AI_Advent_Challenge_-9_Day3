# -*- coding: utf-8 -*-
"""Четыре способа рассуждения над одной задачей.

Способ задаётся ТОЛЬКО system prompt'ом и числом вызовов модели.
Вопрос пользователя при этом не меняется ни в одном режиме.
"""
import time
from concurrent.futures import ThreadPoolExecutor

from llm import LlmError, complete, sum_usage

# Требования к оформлению ответа. Единственная инструкция, общая для всех четырёх
# режимов, включая «прямой ответ»: она не говорит, КАК решать задачу, только как
# оформить вывод. Без неё колонки нечем сравнивать — deepseek-v4-pro сыплет LaTeX
# (\\(, \\frac, \\text — проверено на этом эндпоинте), и в узкой колонке это нечитаемо.
FORMAT_SYSTEM = " ".join(
    [
        "Отвечай на русском языке в простом Markdown.",
        "Разрешены: заголовки уровня ### и ниже, маркированные и нумерованные списки,",
        "**жирный**, *курсив*, `моноширинный` и блоки кода в тройных апострофах.",
        "Формулы и вычисления записывай обычным текстом в одну строку",
        "(например: t = S / v = 1500 / 20 = 75 с).",
        "Категорически не используй LaTeX: ни \\( \\), ни \\[ \\], ни \\frac, ни \\text, ни \\cdot.",
        "Не используй таблицы -- ответ показывается в узкой колонке.",
        'Последней строкой ответа напиши "Ответ: ..." -- одна строка с итогом, без пояснений.',
    ]
)


def with_format(*parts):
    """Собирает system prompt режима: требования к формату плюс инструкции самого режима."""
    return "\n\n".join([FORMAT_SYSTEM, *[p for p in parts if p]])


STEPWISE_SYSTEM = " ".join(
    [
        "Решай задачу строго пошагово.",
        "Сначала перечисли, что дано и что требуется найти.",
        "Затем разбей решение на пронумерованные шаги и после каждого шага выписывай",
        "промежуточный результат, а не только рассуждение.",
        "Перед финалом проверь решение подстановкой или встречной прикидкой.",
        'Последней строкой ответа напиши "Ответ: ..." -- одну строку с итогом и без пояснений.',
    ]
)

PROMPT_ENGINEER_SYSTEM = " ".join(
    [
        "Ты -- инженер промптов. Тебе дают задачу пользователя.",
        "НЕ решай её. Твоя работа -- написать промпт, по которому языковая модель решит",
        "эту задачу максимально точно. В промпте укажи: подходящую роль исполнителя,",
        "метод решения, порядок шагов, типичные ошибки этого класса задач, способ",
        "самопроверки и формат ответа.",
        "Верни только текст промпта, без вступлений, комментариев и кавычек вокруг него.",
    ]
)

# Критик в параллельной схеме не критикует чужие решения — при одновременном
# запуске их ещё нет. Вместо этого он сначала перечисляет ловушки задачи,
# а потом решает сам, обходя их.
EXPERTS = [
    {
        "id": "analyst",
        "title": "Аналитик",
        "system": " ".join(
            [
                "Ты -- аналитик. Начни с разбора условия: выпиши все данные, явные и неявные",
                "допущения, что именно требуется найти и чего в условии не хватает.",
                "Только после разбора дай своё решение и итоговый ответ.",
                'Пиши сжато, без воды. Последней строкой -- "Ответ: ...".',
            ]
        ),
    },
    {
        "id": "engineer",
        "title": "Инженер",
        "system": " ".join(
            [
                "Ты -- инженер. Тебя интересует работающая процедура, а не рассуждения",
                "вокруг задачи. Дай конкретный алгоритм или расчёт и доведи его до числа,",
                "формулы или готового ответа. Если задача вычислительная -- покажи вычисления.",
                "Если алгоритмическая -- покажи алгоритм и его сложность.",
                'Пиши сжато. Последней строкой -- "Ответ: ...".',
            ]
        ),
    },
    {
        "id": "critic",
        "title": "Критик",
        "system": " ".join(
            [
                "Ты -- критик и скептик. Сначала перечисли ловушки этой задачи и типичные",
                "ошибки, на которых обычно спотыкаются при её решении: неверная трактовка",
                "условия, подмена вопроса, ошибки в арифметике, забытые граничные случаи.",
                "Затем реши задачу сам, обходя перечисленные ловушки.",
                'Пиши сжато. Последней строкой -- "Ответ: ...".',
            ]
        ),
    },
]

MODERATOR_SYSTEM = " ".join(
    [
        "Ты -- модератор экспертного совета. Ниже даны независимые мнения трёх экспертов",
        "по одной и той же задаче. Сопоставь их: явно назови, в чём они сходятся и в чём",
        "расходятся, и если расходятся -- реши, кто прав, и объясни почему.",
        'Заверши разбор итоговым ответом. Последней строкой -- "Ответ: ..." без пояснений.',
    ]
)


def _done(text, results, stages=None):
    """Единый контракт результата режима."""
    return {
        "text": text,
        "stages": stages or [],
        "usage": sum_usage(results),
        "calls": len(results),
        # последний вызов всегда основной: у direct и stepwise он единственный,
        # у metaprompt это решатель, у council -- модератор
        "finish_reason": results[-1]["finish_reason"],
    }


def run_direct(question):
    """Прямой ответ: 1 вызов, никаких инструкций о способе решения."""
    result = complete([{"role": "user", "content": question}], system=with_format())
    return _done(result["text"], [result])


def run_stepwise(question):
    """Пошагово: 1 вызов, промпт требует разбить решение на шаги и проверить его."""
    result = complete(
        [{"role": "user", "content": question}], system=with_format(STEPWISE_SYSTEM)
    )
    return _done(result["text"], [result])


def run_metaprompt(question):
    """Предпромпт: вызов 1 пишет промпт, вызов 2 решает задачу по нему."""
    generator = complete(
        [{"role": "user", "content": question}], system=PROMPT_ENGINEER_SYSTEM
    )
    solver = complete(
        [{"role": "user", "content": question}], system=with_format(generator["text"])
    )
    return _done(
        solver["text"],
        [generator, solver],
        [{"title": "Промпт, который составила модель", "text": generator["text"]}],
    )


def run_council(question):
    """Совет экспертов: 3 роли параллельно, затем модератор сводит мнения. 4 вызова."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(
                complete,
                [{"role": "user", "content": question}],
                with_format(expert["system"]),
            )
            for expert in EXPERTS
        ]
        settled = []
        for future in futures:
            try:
                settled.append(("ok", future.result()))
            except Exception as e:  # noqa: BLE001 — падение одного эксперта не рушит режим
                settled.append(("fail", e))

    stages = []
    results = []
    opinions = []

    for expert, (status, value) in zip(EXPERTS, settled):
        if status == "ok":
            stages.append(
                {
                    "title": f"Мнение: {expert['title']}",
                    "text": value["text"],
                    "finish_reason": value["finish_reason"],
                }
            )
            results.append(value)
            opinions.append(f"### {expert['title']}\n{value['text']}")
        else:
            stages.append(
                {
                    "title": f"Мнение: {expert['title']} -- ошибка",
                    "text": str(value),
                    "failed": True,
                }
            )

    if not opinions:
        raise LlmError("Все три эксперта вернули ошибку.")

    moderator = complete(
        [
            {
                "role": "user",
                "content": (
                    f"Задача:\n{question}\n\nМнения экспертов:\n\n" + "\n\n".join(opinions)
                ),
            }
        ],
        system=with_format(MODERATOR_SYSTEM),
    )
    results.append(moderator)

    return _done(moderator["text"], results, stages)


# Реестр режимов. Фронтенд рендерит колонки по этому же порядку.
# Цвет каждого режима задан в styles.css и продублирован здесь для подписи.
MODES = {
    "direct": {
        "label": "Прямой ответ",
        "hint": "никаких инструкций по решению, только формат вывода",
        "calls": 1,
        "color": "white",
        "run": run_direct,
    },
    "stepwise": {
        "label": "Пошагово",
        "hint": "промпт требует разбить решение на шаги и проверить его",
        "calls": 1,
        "color": "yellow",
        "run": run_stepwise,
    },
    "metaprompt": {
        "label": "Предпромпт",
        "hint": "модель пишет промпт, потом решает по нему",
        "calls": 2,
        "color": "blue",
        "run": run_metaprompt,
    },
    "council": {
        "label": "Совет экспертов",
        "hint": "аналитик, инженер и критик параллельно, затем синтез",
        "calls": 4,
        "color": "green",
        "run": run_council,
    },
}

MODE_KEYS = list(MODES)


def mode_catalog():
    """Метаданные режимов для фронтенда (без ссылок на функции)."""
    return [
        {
            "key": key,
            "label": mode["label"],
            "hint": mode["hint"],
            "calls": mode["calls"],
            "color": mode["color"],
        }
        for key, mode in MODES.items()
    ]


def run_mode(mode_key, question):
    """Запускает режим и замеряет латентность всей цепочки вызовов."""
    if mode_key not in MODES:
        raise LlmError(f"Неизвестный режим: {mode_key}")

    started = time.time()
    result = MODES[mode_key]["run"](question)
    result["latency_ms"] = round((time.time() - started) * 1000)
    return result
