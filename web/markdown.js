/*
  Мини-рендерер Markdown без сторонних библиотек.

  Принципиально не используется innerHTML: вывод модели собирается в DOM-узлы
  через createTextNode, поэтому текст ответа никогда не попадает в разметку
  и не может исполниться как HTML.
*/
(function () {
  'use strict';

  /** Вытаскивает финальную строку "Ответ: ..." — её показываем отдельной плашкой. */
  function splitVerdict(text) {
    const lines = (text || '').split('\n');
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      const line = lines[i].trim();
      if (!line) continue;
      // строка может быть обёрнута в ** или начинаться со списочного маркера
      const match = line.match(/^[*\->\s]*\**\s*Ответ\s*:\s*(.+?)\s*\**$/i);
      if (match) {
        const verdict = match[1].replace(/\*\*/g, '').trim();
        const body = lines.slice(0, i).concat(lines.slice(i + 1)).join('\n').trim();
        return { verdict, body };
      }
      break; // "Ответ:" ищем только в последней непустой строке
    }
    return { verdict: '', body: (text || '').trim() };
  }

  /**
   * Инлайновая разметка: `код`, **жирный**, *курсив*.
   * Возвращает массив узлов, а не строку.
   */
  function inline(text) {
    const nodes = [];
    // порядок важен: код первым, чтобы ** внутри кода не превращались в <strong>
    const pattern = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)/g;
    let last = 0;
    let match;

    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) {
        nodes.push(document.createTextNode(text.slice(last, match.index)));
      }
      const token = match[0];
      if (token.startsWith('`')) {
        const el = document.createElement('code');
        el.appendChild(document.createTextNode(token.slice(1, -1)));
        nodes.push(el);
      } else if (token.startsWith('**')) {
        const el = document.createElement('strong');
        el.appendChild(document.createTextNode(token.slice(2, -2)));
        nodes.push(el);
      } else {
        const el = document.createElement('em');
        el.appendChild(document.createTextNode(token.slice(1, -1)));
        nodes.push(el);
      }
      last = pattern.lastIndex;
    }

    if (last < text.length) {
      nodes.push(document.createTextNode(text.slice(last)));
    }
    return nodes;
  }

  function appendInline(parent, text) {
    inline(text).forEach((node) => parent.appendChild(node));
  }

  /** Блочный разбор: заголовки, списки, блоки кода, абзацы, разделители. */
  function render(text) {
    const root = document.createElement('div');
    root.className = 'md';

    const lines = (text || '').split('\n');
    let i = 0;
    let paragraph = [];

    function flushParagraph() {
      if (paragraph.length === 0) return;
      const p = document.createElement('p');
      appendInline(p, paragraph.join(' '));
      root.appendChild(p);
      paragraph = [];
    }

    while (i < lines.length) {
      const line = lines[i];
      const trimmed = line.trim();

      // блок кода ```
      if (trimmed.startsWith('```')) {
        flushParagraph();
        i += 1;
        const buffer = [];
        while (i < lines.length && !lines[i].trim().startsWith('```')) {
          buffer.push(lines[i]);
          i += 1;
        }
        i += 1; // закрывающий ```
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.appendChild(document.createTextNode(buffer.join('\n')));
        pre.appendChild(code);
        root.appendChild(pre);
        continue;
      }

      // пустая строка — конец абзаца
      if (!trimmed) {
        flushParagraph();
        i += 1;
        continue;
      }

      // горизонтальный разделитель
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        flushParagraph();
        root.appendChild(document.createElement('hr'));
        i += 1;
        continue;
      }

      // заголовок #..###### — уровни 1-2 прижимаем к h3, крупнее в колонке не нужно
      const heading = trimmed.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        flushParagraph();
        const level = Math.min(Math.max(heading[1].length, 3), 5);
        const el = document.createElement('h' + level);
        appendInline(el, heading[2]);
        root.appendChild(el);
        i += 1;
        continue;
      }

      // списки: маркированный и нумерованный
      const bullet = trimmed.match(/^[-*+]\s+(.*)$/);
      const numbered = trimmed.match(/^\d+[.)]\s+(.*)$/);
      if (bullet || numbered) {
        flushParagraph();
        const ordered = Boolean(numbered);
        const list = document.createElement(ordered ? 'ol' : 'ul');

        while (i < lines.length) {
          const item = lines[i].trim();
          const m = ordered
            ? item.match(/^\d+[.)]\s+(.*)$/)
            : item.match(/^[-*+]\s+(.*)$/);
          if (!m) break;

          const li = document.createElement('li');
          const parts = [m[1]];
          i += 1;
          // продолжение пункта: строки с отступом, не начинающие новый пункт
          while (
            i < lines.length &&
            lines[i].trim() &&
            /^\s{2,}/.test(lines[i]) &&
            !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])
          ) {
            parts.push(lines[i].trim());
            i += 1;
          }
          appendInline(li, parts.join(' '));
          list.appendChild(li);
        }

        root.appendChild(list);
        continue;
      }

      paragraph.push(trimmed);
      i += 1;
    }

    flushParagraph();
    return root;
  }

  window.MD = { render: render, splitVerdict: splitVerdict };
})();
