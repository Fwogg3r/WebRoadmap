// Small Markdown renderer: headings, paragraphs, bold/italics, inline code,
// fenced code, (nested) lists, blockquotes, links. Input is escaped first, so
// raw HTML in notes is shown as text, and only http(s)/relative/# links pass.
"use strict";

const MD = (() => {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function safeUrl(u) {
    u = u.trim();
    if (/^(https?:|mailto:|#|\/|\.{0,2}\/|[\w.-]+(\/|$|#))/i.test(u) && !/^\s*javascript:/i.test(u)) return u;
    return "#";
  }

  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
    s = esc(s);
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, u) =>
      `<a href="${safeUrl(u)}" target="_blank" rel="noopener">${t}</a>`);
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    s = s.replace(/\*\*([^*]+?)\*\*|__([^_]+?)__/g, (_, a, b) => `<strong>${a || b}</strong>`);
    s = s.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_\w])_([^_\s][^_]*?)_(?![_\w])/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
    return s;
  }

  function render(src) {
    const lines = (src || "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let para = [];
    const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
    let i = 0;
    while (i < lines.length) {
      const ln = lines[i];
      let m;
      if (/^\s*```/.test(ln)) {
        flush();
        const code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i++;
        out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
        continue;
      }
      if ((m = /^(#{1,6})\s+(.*)$/.exec(ln))) {
        flush();
        const n = Math.min(m[1].length + 2, 6);
        out.push(`<h${n}>${inline(m[2])}</h${n}>`);
        i++;
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(ln)) {
        flush();
        // Collect the list block, then build nested lists from indentation.
        const items = [];
        while (i < lines.length && (/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
          const l = lines[i];
          const mm = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(l);
          if (mm) items.push({ ind: mm[1].replace(/\t/g, "    ").length, ord: /\d/.test(mm[2]), text: mm[3] });
          else items[items.length - 1].text += " " + l.trim();
          i++;
        }
        const stack = [];
        let html = "";
        for (const it of items) {
          while (stack.length && it.ind < stack[stack.length - 1].ind) html += `</li></${stack.pop().tag}>`;
          const top = stack[stack.length - 1];
          if (!top || it.ind > top.ind) {
            const tag = it.ord ? "ol" : "ul";
            stack.push({ ind: it.ind, tag });
            html += `<${tag}><li>`;
          } else html += "</li><li>";
          html += inline(it.text);
        }
        while (stack.length) html += `</li></${stack.pop().tag}>`;
        out.push(html);
        continue;
      }
      if (/^\s*>/.test(ln)) {
        flush();
        const q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
        out.push(`<blockquote>${render(q.join("\n"))}</blockquote>`);
        continue;
      }
      if (/^\s*(---|\*\*\*)\s*$/.test(ln)) { flush(); out.push("<hr>"); i++; continue; }
      if (!ln.trim()) { flush(); i++; continue; }
      para.push(ln.trim());
      i++;
    }
    flush();
    return out.join("\n");
  }

  // Plain text for search and compact previews.
  const text = (src) => (src || "").replace(/[*_`#>\[\]]/g, "").replace(/\(([^)]*)\)/g, "");

  return { render, inline, text, esc };
})();
