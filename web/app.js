/*
  Один экран, четыре колонки: вопрос уходит во все режимы сразу.
  Бэкенд возвращает результаты всех четырёх режимов одним ответом на /api/run.
*/
(function () {
  'use strict';

  var CONFIG = { modes: [], model: '', maxTokens: 0 };
  var busy = false;

  var els = {
    note: document.getElementById('app-note'),
    question: document.getElementById('question-text'),
    compare: document.getElementById('compare'),
    input: document.getElementById('input'),
    send: document.getElementById('send'),
  };

  /** Русская форма слова «вызов» по числу. */
  function plural(n, one, few, many) {
    var mod10 = n % 10;
    var mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.appendChild(document.createTextNode(text));
    return node;
  }

  /** Строит каркас четырёх карточек по данным /api/config. */
  function buildPanels() {
    els.compare.textContent = '';
    CONFIG.modes.forEach(function (mode) {
      var panel = el('section', 'panel');
      panel.id = 'panel-' + mode.key;
      // цвет режима: белый / жёлтый / синий / зелёный
      panel.style.setProperty('--accent', 'var(--mode-' + mode.key + ')');

      var head = el('div', 'panel__head');
      head.appendChild(el('h2', null, mode.label));
      head.appendChild(el('p', 'panel__subtitle', mode.hint));
      panel.appendChild(head);

      var body = el('div', 'panel__body');
      body.id = 'body-' + mode.key;
      body.appendChild(el('span', 'muted', 'Ответ появится здесь'));
      panel.appendChild(body);

      els.compare.appendChild(panel);
    });
  }

  /** Мета-строка: время, токены, число вызовов, предупреждение о finish_reason. */
  function metaRow(state) {
    var meta = el('div', 'meta');
    meta.appendChild(el('span', null, (state.latency_ms / 1000).toFixed(1) + ' с'));

    var usage = state.usage || {};
    meta.appendChild(el('span', null, (usage.total_tokens || 0) + ' tok'));

    if (usage.reasoning_tokens) {
      meta.appendChild(el('span', null, usage.reasoning_tokens + ' reasoning'));
    }

    meta.appendChild(
      el('span', null, state.calls + ' ' + plural(state.calls, 'вызов', 'вызова', 'вызовов'))
    );

    if (state.finish_reason && state.finish_reason !== 'stop') {
      meta.appendChild(el('span', 'meta__warn', 'finish: ' + state.finish_reason));
    }
    return meta;
  }

  /**
   * Ответ модели. Модель рассуждающая: если бюджет max_tokens ушёл на
   * внутреннее рассуждение, придёт finish_reason "length" и пустой content --
   * без пояснения колонка выглядела бы просто пустой.
   */
  function answerNode(text, finishReason) {
    var wrap = document.createDocumentFragment();

    if (!text || !text.trim()) {
      var msg =
        finishReason === 'length'
          ? 'Модель не вернула текст: весь бюджет max_tokens (' +
            CONFIG.maxTokens +
            ') ушёл на внутреннее рассуждение. Поднимите MAX_TOKENS в app/llm.py или упростите вопрос.'
          : 'Модель вернула пустой ответ (finish_reason: ' + (finishReason || 'неизвестно') + ').';
      wrap.appendChild(el('div', 'answer--empty', msg));
      return wrap;
    }

    var split = window.MD.splitVerdict(text);
    if (split.verdict) {
      var verdict = el('div', 'verdict');
      verdict.appendChild(el('span', 'verdict__label', 'Ответ'));
      verdict.appendChild(el('span', 'verdict__value', split.verdict));
      wrap.appendChild(verdict);
    }
    wrap.appendChild(window.MD.render(split.body));
    return wrap;
  }

  /** Промежуточные артефакты: сгенерированный промпт, мнения экспертов. */
  function stagesNode(stages) {
    if (!stages || stages.length === 0) return null;
    var box = el('div', 'stages');

    stages.forEach(function (stage) {
      var details = document.createElement('details');
      var summary = el('summary', stage.failed ? 'stages__fail' : null, stage.title);
      details.appendChild(summary);

      var body = el('div', 'stages__body');
      if (stage.failed) {
        body.appendChild(el('div', 'answer--empty', stage.text));
      } else {
        body.appendChild(answerNode(stage.text, stage.finish_reason));
      }
      details.appendChild(body);
      box.appendChild(details);
    });

    return box;
  }

  function renderState(modeKey, state) {
    var panel = document.getElementById('panel-' + modeKey);
    var body = document.getElementById('body-' + modeKey);
    if (!panel || !body) return;

    // мета-строка живёт между head и body, пересобираем её каждый раз
    var oldMeta = panel.querySelector('.meta');
    if (oldMeta) oldMeta.remove();

    body.textContent = '';

    if (state.status === 'loading') {
      var loading = el('span', 'muted', 'думаю');
      loading.appendChild(el('span', 'cursor'));
      body.appendChild(loading);
      return;
    }

    if (state.status === 'error') {
      body.appendChild(el('span', 'panel__err', state.error));
      return;
    }

    if (state.status === 'done') {
      panel.insertBefore(metaRow(state), body);
      var stages = stagesNode(state.stages);
      if (stages) body.appendChild(stages);
      body.appendChild(answerNode(state.text, state.finish_reason));
    }
  }

  function setBusy(value) {
    busy = value;
    els.input.disabled = value;
    els.send.disabled = value || !els.input.value.trim();
    els.send.textContent = value ? 'Работаю…' : 'Сравнить все 4';
  }

  async function send() {
    var question = els.input.value.trim();
    if (!question || busy) return;

    els.input.value = '';
    els.question.textContent = question;
    els.question.className = '';
    setBusy(true);

    CONFIG.modes.forEach(function (mode) {
      renderState(mode.key, { status: 'loading' });
    });

    try {
      var response = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question }),
      });

      var data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'HTTP ' + response.status);
      }

      CONFIG.modes.forEach(function (mode) {
        renderState(mode.key, data.results[mode.key] || {
          status: 'error',
          error: 'Режим не вернул результат',
        });
      });
    } catch (e) {
      CONFIG.modes.forEach(function (mode) {
        renderState(mode.key, { status: 'error', error: String(e.message || e) });
      });
    } finally {
      setBusy(false);
    }
  }

  els.send.addEventListener('click', send);

  els.input.addEventListener('input', function () {
    els.send.disabled = busy || !els.input.value.trim();
  });

  els.input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  // Стартовая загрузка конфигурации: модель и список режимов приходят с сервера
  fetch('/api/config')
    .then(function (r) {
      return r.json();
    })
    .then(function (config) {
      CONFIG = config;
      var totalCalls = config.modes.reduce(function (sum, m) {
        return sum + m.calls;
      }, 0);
      els.note.textContent = 'модель ' + config.model + ' · ' + totalCalls + ' запросов к API';
      document.querySelector('.app__input-note').textContent = totalCalls + ' запросов к API';
      buildPanels();
      setBusy(false);
    })
    .catch(function (e) {
      els.note.textContent = 'не удалось загрузить конфигурацию: ' + e.message;
    });
})();
