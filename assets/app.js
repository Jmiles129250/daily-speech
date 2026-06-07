(function () {
  "use strict";

  var root = document.getElementById("speeches");
  var searchInput = document.getElementById("search-input");
  var searchStatus = document.getElementById("search-status");
  if (!root) return;

  // --- time helpers ---------------------------------------------------------

  function hexToBg(hex) {
    // Convert "#rrggbb" to "rgba(r, g, b, 0.1)" for soft background tints.
    if (!hex || hex[0] !== "#" || hex.length !== 7) return "rgba(47, 125, 107, 0.1)";
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + ", " + g + ", " + b + ", 0.12)";
  }

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

  // --- escape / highlight helpers ------------------------------------------

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeRegex(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /**
   * Highlight non-overlapping matches of `keywords` inside `text` (case-insensitive).
   * `text` and keywords are assumed already lowercased for the matching step, but
   * we render with original casing intact.
   */
  function highlight(text, keywords) {
    if (!keywords.length) return escapeHtml(text);
    var pattern = keywords.map(escapeRegex).join("|");
    var re = new RegExp("(" + pattern + ")", "gi");
    var out = "";
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      out += escapeHtml(text.slice(last, m.index));
      out += "<mark>" + escapeHtml(m[0]) + "</mark>";
      last = m.index + m[0].length;
      if (m.index === re.lastIndex) re.lastIndex++; // avoid zero-width
    }
    out += escapeHtml(text.slice(last));
    return out;
  }

  function renderInline(text, keywords) {
    var escaped = escapeHtml(text);
    if (!keywords.length) return escaped;
    var pattern = keywords.map(escapeRegex).join("|");
    var re = new RegExp("(" + pattern + ")", "gi");
    return escaped.replace(re, "<mark>$1</mark>");
  }

  function renderMarkdown(body, keywords) {
    if (!body) return "";
    var lines = body.replace(/\r\n/g, "\n").split("\n");
    var out = [];
    var blockLines = [];

    function flushBlock() {
      if (blockLines.length) {
        var inner = blockLines
          .map(function (l) { return renderInline(l, keywords); })
          .join("<br />");
        out.push("<blockquote>" + inner + "</blockquote>");
      }
      blockLines = [];
    }

    for (var i = 0; i < lines.length; i++) {
      var raw = lines[i];
      var line = raw.replace(/\s+$/g, "");

      if (line.indexOf("# ") === 0) continue; // strip leading title
      if (line.indexOf("## ") === 0) {
        flushBlock();
        out.push("<h2>" + renderInline(line.slice(3), keywords) + "</h2>");
        continue;
      }
      if (line.indexOf("> ") === 0) {
        blockLines.push(line.slice(2));
        continue;
      }
      if (line.trim() === "") {
        flushBlock();
        continue;
      }
      flushBlock();
      out.push("<p>" + renderInline(line, keywords) + "</p>");
    }
    flushBlock();
    return out.join("\n");
  }

  // --- body fetch + cache --------------------------------------------------

  var bodyCache = Object.create(null); // file -> Promise<{title, body}>

  // --- collections ---------------------------------------------------------

  function buildCollectionBar() {
    var bar = document.getElementById("collection-bar");
    if (!bar) return;
    bar.innerHTML = "";

    var counts = computeCollectionCounts();
    var items = [{ name: "__all__", label: "全部", color: null, count: state.entries.length }];
    state.collections.forEach(function (c) {
      items.push({
        name: c.name,
        label: c.name + (c.description ? " · " + c.description : ""),
        color: c.color,
        count: counts[c.name] || 0,
      });
    });

    items.forEach(function (item) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "collection-chip" + (state.activeCollection === item.name ? " active" : "");
      chip.setAttribute("data-collection", item.name);
      if (item.color) chip.style.setProperty("--chip-dot", item.color);

      if (item.color) {
        var dot = document.createElement("span");
        dot.className = "dot";
        chip.appendChild(dot);
      }
      var label = document.createElement("span");
      label.textContent = item.label;
      chip.appendChild(label);
      var count = document.createElement("span");
      count.className = "count";
      count.textContent = String(item.count);
      chip.appendChild(count);

      chip.addEventListener("click", function () {
        state.activeCollection = item.name;
        Array.prototype.forEach.call(bar.children, function (el) {
          el.classList.toggle("active", el.getAttribute("data-collection") === item.name);
        });
        applyCollectionFilter();
      });
      bar.appendChild(chip);
    });
  }

  function computeCollectionCounts() {
    var out = {};
    state.entries.forEach(function (e) {
      var c = e.collection || "default";
      out[c] = (out[c] || 0) + 1;
    });
    return out;
  }

  function applyCollectionFilter() {
    var active = state.activeCollection;
    state.cards.forEach(function (card) {
      var matches =
        active === "__all__" || (card.entry.collection || "default") === active;
      card.el.style.display = matches ? "" : "none";
    });
  }

  function loadSpeechBody(file) {
    if (bodyCache[file]) return bodyCache[file];
    var p = fetch(file + "?v=2", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      })
      .then(function (md) {
        var body = md;
        var title = "";
        if (md.indexOf("---") === 0) {
          var end = md.indexOf("\n---", 3);
          if (end > -1) {
            var fm = md.slice(3, end);
            var t = fm.match(/^title:\s*(.+)$/m);
            if (t) title = t[1].trim();
            body = md.slice(end + 4).replace(/^\s*\n/, "");
          }
        }
        return { title: title, body: body };
      });
    bodyCache[file] = p;
    return p;
  }

  // --- card model ----------------------------------------------------------

  var state = {
    entries: [],
    today: todayInShanghai(),
    cards: new Map(), // date -> {el, entry, body, isToday}
    searchTokens: [],
    searchActive: false,
    collections: [], // [{name, description, color}]
    activeCollection: "__all__", // "__all__" or collection name
  };

  function makeCardEl(entry, opts) {
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

    // collection label (always show, helps user identify the collection at a glance)
    var collectionName = entry.collection || "default";
    if (collectionName && collectionName !== "default") {
      var coll = state.collections.find(function (c) { return c.name === collectionName; });
      var color = coll ? coll.color : "#2f7d6b";
      var labelEl = document.createElement("span");
      labelEl.className = "collection-label";
      labelEl.textContent = collectionName;
      labelEl.style.setProperty("--chip-bg", hexToBg(color));
      labelEl.style.setProperty("--chip-fg", color);
      left.appendChild(labelEl);
    }

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

    function expand(rendered) {
      inner.innerHTML = rendered;
      card.classList.add("expanded");
      // two-frame to allow CSS transition to read scrollHeight
      requestAnimationFrame(function () {
        body.style.maxHeight = body.scrollHeight + "px";
      });
    }

    function collapse() {
      card.classList.remove("expanded");
      body.style.maxHeight = "0px";
    }

    // toggle
    function toggle() {
      if (card.classList.contains("expanded")) {
        collapse();
        return;
      }
      loadSpeechBody(entry.file).then(function (r) {
        expand(renderMarkdown(r.body, []));
      });
    }
    header.addEventListener("click", toggle);
    excerpt.addEventListener("click", toggle);

    return {
      el: card,
      entry: entry,
      isToday: isToday,
      titleEl: title,
      excerptEl: excerpt,
      innerEl: inner,
      bodyEl: body,
      expand: expand,
      collapse: collapse,
      setHighlight: function (keywords) {
        title.innerHTML = keywords.length
          ? highlight(entry.title, keywords)
          : escapeHtml(entry.title);
        if (excerpt) {
          excerpt.innerHTML = keywords.length
            ? highlight(entry.excerpt, keywords)
            : escapeHtml(entry.excerpt);
        }
      },
      renderBody: function (body, keywords) {
        inner.innerHTML = renderMarkdown(body, keywords);
        if (keywords.length) {
          card.classList.add("is-hit");
        } else {
          card.classList.remove("is-hit");
        }
      },
      setBodyHeight: function () {
        if (card.classList.contains("expanded")) {
          body.style.maxHeight = body.scrollHeight + "px";
        }
      },
    };
  }

  // --- search --------------------------------------------------------------

  function parseQuery(q) {
    return (q || "")
      .toLowerCase()
      .split(/\s+/)
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
  }

  function entryMatches(entry, body, tokens) {
    if (!tokens.length) return true;
    var haystack = (
      entry.date +
      " " +
      entry.title +
      " " +
      (entry.excerpt || "") +
      " " +
      (body || "")
    ).toLowerCase();
    for (var i = 0; i < tokens.length; i++) {
      if (haystack.indexOf(tokens[i]) === -1) return false;
    }
    return true;
  }

  function setStatus(text) {
    if (!searchStatus) return;
    if (!text) {
      searchStatus.textContent = "";
      return;
    }
    searchStatus.innerHTML = text;
  }

  function applySearch() {
    var tokens = state.searchTokens;
    var active = tokens.length > 0;
    state.searchActive = active;
    var activeCollection = state.activeCollection || "__all__";

    function inActiveCollection(card) {
      return (
        activeCollection === "__all__" ||
        (card.entry.collection || "default") === activeCollection
      );
    }

    if (!active) {
      // restore default
      setStatus("");
      state.cards.forEach(function (card) {
        card.el.style.display = inActiveCollection(card) ? "" : "none";
        card.setHighlight([]);
        if (card.bodyLoaded) {
          loadSpeechBody(card.entry.file).then(function (r) {
            card.renderBody(r.body, []);
            card.setBodyHeight();
          });
        }
        if (card.isToday) {
          if (!card.bodyLoaded) {
            loadSpeechBody(card.entry.file).then(function (r) {
              card.renderBody(r.body, []);
              card.setBodyHeight();
              card.bodyLoaded = true;
            });
          } else {
            card.setBodyHeight();
          }
        } else {
          card.collapse();
        }
      });
      return;
    }

    // active search: for each card, fetch body if needed, render with highlights,
    // expand on hit, hide on miss. Also respect active collection filter.
    var hits = 0;
    var pending = [];
    state.cards.forEach(function (card) {
      if (!inActiveCollection(card)) {
        card.el.style.display = "none";
        return;
      }
      var p = loadSpeechBody(card.entry.file).then(function (r) {
        var matched = entryMatches(card.entry, r.body, tokens);
        if (matched) {
          hits++;
          card.el.style.display = "";
          card.setHighlight(tokens);
          card.renderBody(r.body, tokens);
          card.el.classList.add("expanded");
          card.setBodyHeight();
          card.bodyLoaded = true;
        } else {
          card.el.style.display = "none";
        }
      });
      pending.push(p);
    });
    Promise.all(pending).then(function () {
      if (hits === 0) {
        setStatus("没有找到匹配的演讲稿。");
      } else if (hits === 1) {
        setStatus("找到 <span class=\"count\">1</span> 篇匹配的演讲稿。");
      } else {
        setStatus("找到 <span class=\"count\">" + hits + "</span> 篇匹配的演讲稿。");
      }
    });
  }

  // debounce
  var searchTimer = null;
  function onSearchInput() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.searchTokens = parseQuery(searchInput.value);
      applySearch();
    }, 180);
  }

  // --- bootstrap -----------------------------------------------------------

  function showEmpty(msg) {
    root.innerHTML = "";
    var p = document.createElement("p");
    p.className = "empty";
    p.textContent = msg;
    root.appendChild(p);
  }

  function bootstrap() {
    var today = todayInShanghai();
    state.today = today;
    var collectionsP = fetch("speeches/collections.json?v=2", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
    var manifestP = fetch("speeches/manifest.json?v=2", { cache: "no-store" })
      .then(function (r) {
        if (r.status === 404) {
          showEmpty("暂无内容，等待每日 06:30 自动生成。");
          return null;
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      });
    Promise.all([collectionsP, manifestP])
      .then(function (results) {
        var collectionsDoc = results[0];
        var manifest = results[1];
        state.collections =
          (collectionsDoc && Array.isArray(collectionsDoc.collections))
            ? collectionsDoc.collections
            : [{ name: "default", description: "默认合集", color: "#2f7d6b" }];
        if (!manifest) return;
        var entries = (manifest.entries || []).slice();
        entries.sort(function (a, b) {
          return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
        });
        if (!entries.length) {
          showEmpty("暂无内容，等待每日 06:30 自动生成。");
          return;
        }
        state.entries = entries;
        root.innerHTML = "";
        entries.forEach(function (entry) {
          var isToday = entry.date === today;
          var card = makeCardEl(entry, { isToday: isToday });
          state.cards.set(entry.date, card);
          root.appendChild(card.el);
          if (isToday) {
            loadSpeechBody(entry.file).then(function (r) {
              card.renderBody(r.body, []);
              card.setBodyHeight();
              card.bodyLoaded = true;
            });
          }
        });
        buildCollectionBar();
        if (searchInput) {
          searchInput.addEventListener("input", onSearchInput);
          searchInput.addEventListener("search", onSearchInput);
        }
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
