// The notepad for your own notes, during a call and after it: a small rich-text editor that saves Markdown,
// which is what Claude reads. It works like Notes or Granola:
//   "- " or "* " starts a bullet list, "1. " a numbered one, "# " a heading
//   Tab / ⇧Tab indent and outdent a bullet · Return on an empty bullet ends the list · ⌫ at the start of a
//   bullet takes the bullet away
//   ⌘B bold · ⌘I italic · ⌘U underline · ⌘⇧X strikethrough · ⌘⇧8 bullets · ⌘⇧7 numbers · ⌘⌥1 heading ·
//   ⌘⌥0 plain text
// Pasted text comes in plain, with its bullets turned into lists.
(() => {
  const INDENT = '    ';  // Markdown nests lists four spaces deep
  const BLOCKS = new Set(['DIV', 'P', 'UL', 'OL', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE', 'PRE']);
  const MARKER = /^( *)([-*+•◦▪●○■]|\d+[.)])\s+(.*)$/;
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // -- Markdown → editor -------------------------------------------------------------------------

  function inlineHtml(text) {
    return esc(text)
      .replace(/&lt;u&gt;(.+?)&lt;\/u&gt;/g, '<u>$1</u>')
      .replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, '<b>$1</b>')
      .replace(/__(?=\S)(.+?)(?<=\S)__/g, '<b>$1</b>')
      .replace(/~~(?=\S)(.+?)(?<=\S)~~/g, '<s>$1</s>')
      .replace(/(^|[^\w*])\*(?=[^\s*])([^*]*?[^\s*])?\*(?![\w*])/g, (m, pre, body) => (body === undefined ? m : `${pre}<i>${body}</i>`))
      .replace(/(^|[^\w])_(?=[^\s_])([^_]*?[^\s_])?_(?!\w)/g, (m, pre, body) => (body === undefined ? m : `${pre}<i>${body}</i>`));
  }

  function toHtml(md) {
    const out = [];
    const open = [];  // lists still open, innermost last: { tag, indent }
    const closeTo = (depth) => { while (open.length > depth) out.push(`</li></${open.pop().tag}>`); };
    for (const raw of (md || '').replace(/\r\n?/g, '\n').split('\n')) {
      const line = raw.replace(/\t/g, INDENT);
      const m = MARKER.exec(line);
      if (m) {
        const indent = m[1].length, tag = /\d/.test(m[2]) ? 'ol' : 'ul';
        while (open.length && open[open.length - 1].indent > indent) closeTo(open.length - 1);
        if (open.length && open[open.length - 1].indent === indent && open[open.length - 1].tag !== tag) closeTo(open.length - 1);
        const top = open[open.length - 1];
        if (top && top.indent === indent) out.push('</li><li>');
        else {  // a new list, inside the item that's open (if any)
          out.push(`<${tag}><li>`);
          open.push({ tag, indent });
        }
        out.push(inlineHtml(m[3]) || '<br>');
      } else if (open.length && /^ +\S/.test(line)) {
        out.push(`<br>${inlineHtml(line.trim())}`);  // more of the same bullet
      } else {
        closeTo(0);
        const head = /^#{1,6}\s+(.*)$/.exec(line);
        out.push(head ? `<h3>${inlineHtml(head[1]) || '<br>'}</h3>` : `<div>${inlineHtml(line) || '<br>'}</div>`);
      }
    }
    closeTo(0);
    return out.join('') || '<div><br></div>';
  }

  // -- editor → Markdown -------------------------------------------------------------------------

  function wrap(el, text) {
    const st = el.style;
    const tag = el.tagName;
    const marks = [];
    if (/^(B|STRONG)$/.test(tag) || st.fontWeight === 'bold' || Number(st.fontWeight) >= 600) marks.push(['**', '**']);
    if (/^(I|EM)$/.test(tag) || st.fontStyle === 'italic') marks.push(['*', '*']);
    if (/^(S|STRIKE|DEL)$/.test(tag) || /line-through/.test(st.textDecorationLine || st.textDecoration || '')) marks.push(['~~', '~~']);
    if (tag === 'U' || /underline/.test(st.textDecorationLine || st.textDecoration || '')) marks.push(['<u>', '</u>']);
    const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(text);
    if (!marks.length || !m[2].trim()) return text;
    // markers hug the words; spaces around them stay outside, or Markdown wouldn't see them
    return m[1] + marks.map((x) => x[0]).join('') + m[2] + marks.map((x) => x[1]).reverse().join('') + m[3];
  }

  function inlineMd(node) {
    let s = '';
    for (const n of node.childNodes) {
      if (n.nodeType === Node.TEXT_NODE) s += n.nodeValue.replace(/ /g, ' ');
      else if (n.nodeType !== Node.ELEMENT_NODE) continue;
      else if (n.tagName === 'BR') s += '\n';
      else if (n.tagName === 'UL' || n.tagName === 'OL') continue;  // a nested list is lines of its own
      else if (BLOCKS.has(n.tagName)) s += `\n${inlineMd(n)}\n`;
      else s += wrap(n, inlineMd(n));
    }
    return s;
  }

  const blockLines = (text) => text.replace(/\n$/, '').split('\n');  // a block's last <br> just holds it open

  function toMd(root) {
    const lines = [];
    let loose = '';  // text typed straight into the editor rather than into a line of its own
    const flush = () => { if (loose) lines.push(...blockLines(loose)); loose = ''; };
    const list = (el, depth) => {
      let n = 0;
      for (const child of el.children) {
        if (child.tagName === 'LI') {
          n += 1;
          const text = inlineMd(child).replace(/^\n+|\n+$/g, '').replace(/\n{2,}/g, '\n');
          const [first, ...more] = text.split('\n');
          lines.push(`${INDENT.repeat(depth)}${el.tagName === 'OL' ? `${n}.` : '-'} ${first.trim()}`.trimEnd());
          more.forEach((line) => lines.push(INDENT.repeat(depth + 1) + line.trim()));
          for (const sub of child.querySelectorAll(':scope > ul, :scope > ol, :scope > div > ul, :scope > div > ol')) list(sub, depth + 1);
        } else if (child.tagName === 'UL' || child.tagName === 'OL') {
          list(child, depth + 1);  // Chrome's Tab can put a list straight inside a list
        }
      }
    };
    const walk = (el) => {
      for (const n of el.childNodes) {
        if (n.nodeType === Node.ELEMENT_NODE && (n.tagName === 'UL' || n.tagName === 'OL')) {
          flush();
          list(n, 0);
        } else if (n.nodeType === Node.ELEMENT_NODE && /^H[1-6]$/.test(n.tagName)) {
          flush();
          const text = inlineMd(n).replace(/\s+/g, ' ').trim();
          lines.push(text ? `### ${text}` : '');
        } else if (n.nodeType === Node.ELEMENT_NODE && BLOCKS.has(n.tagName)) {
          flush();
          if ([...n.children].some((c) => BLOCKS.has(c.tagName))) walk(n);
          else lines.push(...blockLines(inlineMd(n)));
        } else if (n.nodeType === Node.ELEMENT_NODE) {
          loose += n.tagName === 'BR' ? '\n' : wrap(n, inlineMd(n));
        } else if (n.nodeType === Node.TEXT_NODE) {
          loose += n.nodeValue.replace(/ /g, ' ');
        }
      }
      flush();
    };
    walk(root);
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    return lines.map((line) => line.trimEnd()).join('\n');
  }

  // -- editing -------------------------------------------------------------------------------------

  const exec = (command, value = null) => document.execCommand(command, false, value);

  function noteEditor({ value = '', placeholder = '', onChange = () => {} } = {}) {
    const el = document.createElement('div');
    el.className = 'notepad editor';
    el.contentEditable = 'true';
    el.spellcheck = true;
    el.setAttribute('role', 'textbox');
    el.setAttribute('aria-multiline', 'true');
    el.setAttribute('aria-label', 'Your notes');
    el.dataset.placeholder = placeholder;

    const blank = () => el.classList.toggle('blank', !el.textContent.trim() && !el.querySelector('li'));
    el.getValue = () => toMd(el);
    el.setValue = (md) => { el.innerHTML = toHtml(md); blank(); };
    el.setValue(value);

    el.addEventListener('focus', () => {
      exec('styleWithCSS', false);              // <b>, not <span style="font-weight: bold">
      exec('defaultParagraphSeparator', 'div'); // each line in its own <div>
    });

    // The line the caret is in, and the list item if it's in one.
    const here = () => {
      const sel = getSelection();
      if (!sel.rangeCount || !el.contains(sel.anchorNode)) return {};
      const node = sel.anchorNode.nodeType === Node.ELEMENT_NODE ? sel.anchorNode : sel.anchorNode.parentElement;
      const li = node.closest('li');
      return { sel, li: li && el.contains(li) ? li : null, heading: node.closest('h1, h2, h3, h4, h5, h6') };
    };
    // Whether the caret is at the very start of this item's own text.
    const atStart = (sel, block) => {
      if (!sel.isCollapsed) return false;
      const r = document.createRange();
      r.setStart(block, 0);
      r.setEnd(sel.anchorNode, sel.anchorOffset);
      return r.toString() === '';
    };
    // In a list inside another list (Chrome nests either inside the outer item or straight inside the outer list).
    const nested = (li) => { const outer = li.parentElement.parentElement; return !!outer && outer !== el && el.contains(outer) && !!outer.closest('li, ul, ol'); };
    const unlist = (li) => exec(li.parentElement.tagName === 'OL' ? 'insertOrderedList' : 'insertUnorderedList');

    // Run a line-changing command without losing your place in the line (Chrome puts the caret at its start).
    const inPlace = (fn) => {
      const sel = getSelection();
      const at = sel.rangeCount && sel.isCollapsed ? lineBefore(sel.anchorNode, sel.anchorOffset).toString().length : null;
      fn();
      const now = getSelection();
      if (at === null || !now.rangeCount) return;
      for (let i = lineBefore(now.anchorNode, now.anchorOffset).toString().length; i < at; i += 1) now.modify('move', 'forward', 'character');
    };

    el.addEventListener('keydown', (e) => {
      const mod = e.metaKey || e.ctrlKey;
      const { sel, li, heading } = here();
      let handled = true;
      if (e.key === 'Tab' && !mod && !e.altKey) {
        if (li) inPlace(() => exec(e.shiftKey ? 'outdent' : 'indent'));  // elsewhere Tab does nothing (rather than leave the notes)
      } else if (mod && !e.altKey && !e.shiftKey && ['b', 'i', 'u'].includes(e.key.toLowerCase())) {
        exec({ b: 'bold', i: 'italic', u: 'underline' }[e.key.toLowerCase()]);
      } else if (mod && e.shiftKey && e.code === 'KeyX') {
        exec('strikeThrough');
      } else if (mod && e.shiftKey && e.code === 'Digit8') {
        inPlace(() => exec('insertUnorderedList'));
      } else if (mod && e.shiftKey && e.code === 'Digit7') {
        inPlace(() => exec('insertOrderedList'));
      } else if (mod && e.altKey && e.code === 'Digit1') {
        inPlace(() => { if (li) unlist(li); exec('formatBlock', heading ? '<div>' : '<h3>'); });
      } else if (mod && e.altKey && e.code === 'Digit0') {
        inPlace(() => (li ? unlist(li) : exec('formatBlock', '<div>')));
      } else if (e.key === 'Backspace' && !mod && !e.altKey && li && sel && atStart(sel, li)) {
        if (nested(li)) exec('outdent');
        else unlist(li);
      } else {
        handled = false;
      }
      if (handled) e.preventDefault();
    });

    // Markdown as you type: "- " at the start of a line makes it a bullet, and so on. (Also when the "- " arrives
    // with more text in one go, as autocorrect or a dictated phrase does; but not when you've undone it.)
    const SHORTHAND = [[/^[-*•] /, ['insertUnorderedList']], [/^1[.)] /, ['insertOrderedList']], [/^#{1,3} /, ['formatBlock', '<h3>']]];
    // The caret's line so far: from just after the last line break, list or block before it.
    const lineBefore = (node, offset) => {
      const r = document.createRange();
      let child = node;
      while (child.parentNode && child.parentNode !== el && !/^(DIV|P|LI|H[1-6])$/.test(child.parentNode.tagName)) child = child.parentNode;
      let prev = child.previousSibling;
      while (prev && !(prev.nodeType === Node.ELEMENT_NODE && (prev.tagName === 'BR' || BLOCKS.has(prev.tagName)))) prev = prev.previousSibling;
      if (prev) r.setStartAfter(prev);
      else r.setStartBefore(child);
      r.setEnd(node, offset);
      return r;
    };
    // The first `length` characters of a line, as a range.
    const firstChars = (line, length) => {
      const walker = document.createTreeWalker(line.commonAncestorContainer.nodeType === Node.TEXT_NODE
        ? line.commonAncestorContainer.parentNode : line.commonAncestorContainer, NodeFilter.SHOW_TEXT);
      let seen = 0, node;
      while ((node = walker.nextNode())) {
        if (!line.intersectsNode(node)) continue;
        const from = node === line.startContainer ? line.startOffset : 0;
        const len = node.nodeValue.length - from;
        if (length <= seen + len) {
          const r = line.cloneRange();
          r.setEnd(node, from + length - seen);
          return r;
        }
        seen += len;
      }
      return null;
    };
    const shorthand = (typedLength) => {
      const { sel, li } = here();
      if (!sel || !sel.isCollapsed || li) return true;
      const before = lineBefore(sel.anchorNode, sel.anchorOffset);
      const text = before.toString().replace(/\u00a0/g, ' ');
      for (const [pattern, command] of SHORTHAND) {
        const m = pattern.exec(text);
        if (!m || m[0].length - 1 < text.length - typedLength) continue;  // its space wasn't just typed
        const marker = firstChars(before, m[0].length);
        if (!marker) return true;
        const caret = sel.getRangeAt(0).cloneRange();
        sel.removeAllRanges();
        sel.addRange(marker);
        if (!exec('delete')) {  // Chrome won't edit from inside an edit that a command (not a key) made: try again after it
          sel.removeAllRanges();
          sel.addRange(caret);
          return false;
        }
        exec(...command);
        for (let i = m[0].length; i < text.length; i += 1) getSelection().modify('move', 'forward', 'character');
        onChange(el.getValue());
        return true;
      }
      return true;
    };
    el.addEventListener('input', (e) => {
      if (e.inputType === 'insertText' && e.data && e.data.includes(' ') && !shorthand(e.data.length)) {
        setTimeout(() => shorthand(e.data.length));
      }
      blank();
      onChange(el.getValue());
    });

    el.addEventListener('paste', (e) => {
      const text = e.clipboardData && e.clipboardData.getData('text/plain');
      if (!text) return;
      e.preventDefault();
      if (!text.includes('\n') && !MARKER.test(text)) exec('insertText', text);
      else exec('insertHTML', toHtml(text.replace(/\n+$/, '')));
    });
    return el;
  }

  window.noteEditor = noteEditor;
  window.notesMarkdown = { toHtml, toMd };
})();
