(function () {
  "use strict";

  var root = document.getElementById("speeches");
  if (!root) return;

  // Asia/Shanghai today as YYYY-MM-DD
  function todayInShanghai() {
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    var y = parts.find(function (p) { return p.type === "year"; }).value;
    var m = parts.find(function (p) { return p.type === "month"; }).value;
    var d = parts.find(function (p) { return p.type === "day"; }).value;
    return y + "-" + m + "-" + d;
  }

  // Tiny markdown -> HTML
  // Supports: # h1 (stripped as title), ## h2, > blockquote, **bold**
  // Paragraphs separated by blank lines.
  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderInline(text) {
    var escaped = escapeHtml(text);
    // bold
    escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<em>$1</em>");
    return escaped;
  }

  function renderMarkdown(body) {
    if (!body) return "";
    var lines = body.replace(/\r\n/g, "\n").split("\n");
    var out = [];
    var inBlock = false;
    var blockLines = [];

    function flushBlock() {
      if (blockLines.length) {
        var inner = blockLines.map(renderInline).join("<br />");
        out.push("<blockquote>" + inner + "</blockquote>");
      }
      blockLines = [];
      inBlock = false;
    }

    for (var i = 0; i < lines.length; i++) {
      var raw = lines[i];
      var line = raw.replace(/\s+$/g, "");

      if (line.indexOf("# ") === 0) {
        // leading title line: skip (title comes from frontmatter)
        continue;
      }
      if (line.indexOf("## ") === 0) {
        flushBlock();
        out.push("<h2>" + renderInline(line.slice(3)) + "</h2>");
        continue;
      }
      if (line.indexOf("> ") === 0) {
        inBlock = true;
        blockLines.push(line.slice(2));
        continue;
      }
      if (line.trim() === "") {
        flushBlock();
        continue;
      }
      flushBlock();
      out.push("<p>" + renderInline(line) + "</p>");
    }
    flushBlock();
    return out.join("\n");
  }

  function makeCard(entry, opts) {
    var isToday = !!opts.isToday;
    var card = document.createElement("article");
    card.className = "card" + (isToday ? " expanded" : "");
    card.setAttribute("data-date", entry.date);

    var header = document.createElement("div");
    header.className = "card-header";

    var left = document.createElement("div");
    left.className = "card-title-row";

    var title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = entry.title;
    left.appendChild(title);

    if (isToday) {
      var badge = document.createElement("span");
      badge.className = "today-badge";
      badge.textContent = "今日";
      left.appendChild(badge);
    }

    var right = document.createElement("div");
    right.style.display = "flex";
    right.style.alignItems = "center";
    right.style.gap = "12px";

    var date = document.createElement("span");
    date.className = "card-date";
    date.textContent = entry.date;
    right.appendChild(date);

    var chev = document.createElement("span");
    chev.className = "chevron";
    chev.textContent = "▾";
    right.appendChild(chev);

    header.appendChild(left);
    header.appendChild(right);

    var excerpt = document.createElement("p");
    excerpt.className = "excerpt";
    excerpt.textContent = entry.excerpt || "";

    var body = document.createElement("div");
    body.className = "card-body";
    var inner = document.createElement("div");
    inner.className = "card-body-inner";
    body.appendChild(inner);

    card.appendChild(header);
    if (!isToday && entry.excerpt) {
      card.appendChild(excerpt);
    }
    card.appendChild(body);

    // toggle handler
    function toggle() {
      var expanded = card.classList.toggle("expanded");
      if (expanded) {
        body.style.maxHeight = body.scrollHeight + "px";
      } else {
        body.style.maxHeight = "0px";
      }
    }
    header.addEventListener("click", toggle);
    excerpt.addEventListener("click", toggle);

    // load body on demand (and on initial expand if today)
    var loaded = false;
    function ensureLoaded(cb) {
      if (loaded) return cb();
      loaded = true;
      var url = entry.file + "?v=1";
      fetch(url, { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.text();
        })
        .then(function (md) {
          // split frontmatter
          var body = md;
          if (md.indexOf("---") === 0) {
            var end = md.indexOf("\n---", 3);
            if (end > -1) {
              body = md.slice(end + 4).replace(/^\s*\n/, "");
            }
          }
          inner.innerHTML = renderMarkdown(body);
          cb();
        })
        .catch(function (err) {
          inner.innerHTML =
            '<p style="color:#a33">内容加载失败：' +
            escapeHtml(String(err)) +
            "</p>";
          cb();
        });
    }

    if (isToday) {
      // expand today on first paint
      ensureLoaded(function () {
        // give the layout a tick to settle
        requestAnimationFrame(function () {
          body.style.maxHeight = body.scrollHeight + "px";
        });
      });
    } else {
      // load lazily when user expands
      var origToggle = toggle;
      header.removeEventListener("click", toggle);
      excerpt.removeEventListener("click", toggle);
      function lazyToggle() {
        var willExpand = !card.classList.contains("expanded");
        if (willExpand) {
          ensureLoaded(function () {
            card.classList.add("expanded");
            body.style.maxHeight = body.scrollHeight + "px";
          });
        } else {
          card.classList.remove("expanded");
          body.style.maxHeight = "0px";
        }
      }
      header.addEventListener("click", lazyToggle);
      excerpt.addEventListener("click", lazyToggle);
    }

    return card;
  }

  function showEmpty(msg) {
    root.innerHTML = "";
    var p = document.createElement("p");
    p.className = "empty";
    p.textContent = msg;
    root.appendChild(p);
  }

  function bootstrap() {
    var today = todayInShanghai();
    fetch("speeches/manifest.json?v=1", { cache: "no-store" })
      .then(function (r) {
        if (r.status === 404) {
          showEmpty("暂无内容，等待每日 06:30 自动生成。");
          return null;
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (manifest) {
        if (!manifest) return;
        var entries = (manifest.entries || []).slice();
        entries.sort(function (a, b) {
          return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
        });
        if (!entries.length) {
          showEmpty("暂无内容，等待每日 06:30 自动生成。");
          return;
        }
        root.innerHTML = "";
        entries.forEach(function (entry) {
          var isToday = entry.date === today;
          root.appendChild(makeCard(entry, { isToday: isToday }));
        });
      })
      .catch(function (err) {
        showEmpty("加载失败：" + (err && err.message ? err.message : err));
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
