/* ============================================================
   Draws a saved post's blocks as HTML. Shared by blog.html (the
   public page) and write.html (Preview), so the preview is the
   real thing. Needs DOMPurify loaded first; styles are in
   blog-post.css.
   ============================================================ */

(function () {
  "use strict";

  var UPLOAD_URL = /^\/uploads\/\d{4}\/\d{2}\/[a-f0-9]{32}\.(jpg|png)$/;

  /* Notion's palette by Notion's names; blog-post.css styles each one.
     Checked here as well as on the server, so a value is only ever one
     of these before it reaches an attribute. */
  var HUES = ["gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"];
  var BLENDS = ["sunset", "ocean", "aurora", "candy", "gold"];   // multi-colour gradients
  var COLORS = ["default"].concat(
    HUES,
    HUES.map(function (h) { return h + "_background"; }),
    HUES.concat(BLENDS).map(function (h) { return h + "_gradient"; })
  );
  var FONTS = ["default", "serif", "mono"];

  /* ============ inline text ============
     Text inside a block can carry the editor's inline formatting. That is
     the one place post content becomes HTML, so it goes through DOMPurify
     with only these tags allowed -- no attributes except a link's href. */
  var INLINE = { ALLOWED_TAGS: ["b", "i", "u", "s", "a", "code", "mark", "br"], ALLOWED_ATTR: ["href"] };

  DOMPurify.addHook("afterSanitizeAttributes", function (node) {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener nofollow");
    }
  });

  function inline(tag, html) {
    var el = document.createElement(tag);
    el.innerHTML = DOMPurify.sanitize(html || "", INLINE);
    return el;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text) { node.textContent = text; }
    return node;
  }

  function formatDate(iso) {
    var d = new Date(iso);
    if (isNaN(d)) { return ""; }
    return d.toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
  }

  /* ============ blocks ============ */

  function renderList(style, items) {
    var list = document.createElement(style === "ordered" ? "ol" : "ul");
    if (style === "checklist") { list.className = "checklist"; }
    (items || []).forEach(function (item) {
      var li = document.createElement("li");
      if (style === "checklist") {
        var done = item.meta && item.meta.checked;
        if (done) { li.className = "done"; }
        li.appendChild(el("span", "box", done ? "☑" : "☐"));
      }
      var content = inline("span", item.content);
      content.className = "item";
      li.appendChild(content);
      if (item.items && item.items.length) { li.appendChild(renderList(style, item.items)); }
      list.appendChild(li);
    });
    return list;
  }

  /* Notion's callout: an emoji gutter beside the text. Its colour comes
     from the block's Color tune like any other block -- see colorOf(). */
  function renderCallout(data) {
    var box = el("div", "callout");
    if (data.emoji) { box.appendChild(el("span", "callout-icon", data.emoji)); }
    var text = inline("div", data.text);
    text.className = "callout-text";
    box.appendChild(text);
    return box;
  }

  /* A new callout in Notion starts grey; every other block starts plain. */
  function colorOf(block) {
    var tune = block.tunes && block.tunes.color;
    var color = tune && tune.color;
    if (COLORS.indexOf(color) >= 0) { return color; }
    return block.type === "callout" ? "gray_background" : "default";
  }

  function renderBlock(block) {
    var d = block.data || {};
    switch (block.type) {
      case "paragraph":
        return inline("p", d.text);
      case "header":
        return inline("h" + ([2, 3, 4].indexOf(d.level) >= 0 ? d.level : 2), d.text);
      case "list":
        return renderList(d.style, d.items);
      case "quote":
        var q = inline("blockquote", d.text);
        if (d.caption) { q.appendChild(inline("cite", d.caption)); }
        return q;
      case "callout":
        return renderCallout(d);
      case "code":
        var pre = document.createElement("pre");
        pre.appendChild(el("code", "", d.code));   // textContent: code is shown, never run
        return pre;
      case "delimiter":
        return document.createElement("hr");
      case "table":
        var table = document.createElement("table");
        (d.content || []).forEach(function (row, i) {
          var tr = document.createElement("tr");
          row.forEach(function (cell) {
            tr.appendChild(inline(d.withHeadings && i === 0 ? "th" : "td", cell));
          });
          table.appendChild(tr);
        });
        return table;
      case "image":
        var url = d.file && d.file.url;
        if (!url || !UPLOAD_URL.test(url)) { return null; }
        var fig = document.createElement("figure");
        if (d.withBorder) { fig.classList.add("border"); }
        if (d.withBackground) { fig.classList.add("backdrop"); }
        if (d.stretched) { fig.classList.add("stretched"); }
        var img = document.createElement("img");
        img.src = url;
        img.loading = "lazy";
        img.alt = inline("span", d.caption).textContent;
        fig.appendChild(img);
        if (d.caption) { fig.appendChild(inline("figcaption", d.caption)); }
        return fig;
      default:
        return null;   // a block type this page does not know is skipped, not guessed at
    }
  }

  /* The dateline and body of a post, ready to drop into a page. `updated`
     is optional and only shown when it falls on a different day. */
  function renderPost(post) {
    var wrap = document.createDocumentFragment();

    var when = "Posted " + formatDate(post.when);
    if (post.updated && post.when && post.updated.slice(0, 10) !== post.when.slice(0, 10)) {
      when += " · updated " + formatDate(post.updated);
    }
    wrap.appendChild(el("p", "post-when", when));

    var body = el("div", "post " + styleClasses(post.style).join(" "));
    (post.blocks || []).forEach(function (block) {
      var node = renderBlock(block);
      if (!node) { return; }
      var color = colorOf(block);
      if (color !== "default" || block.type === "callout") { node.setAttribute("data-ncolor", color); }
      body.appendChild(node);
    });
    wrap.appendChild(body);
    return wrap;
  }

  /* The page Style menu as classes: pg-serif / pg-mono, pg-small. Full
     width is the page shell's job, so it is left to the caller. */
  function styleClasses(style) {
    style = style || {};
    var out = [];
    if (FONTS.indexOf(style.font) > 0) { out.push("pg-" + style.font); }
    if (style.small === true) { out.push("pg-small"); }
    return out;
  }

  window.BlogRender = {
    renderPost: renderPost,
    formatDate: formatDate,
    styleClasses: styleClasses,
    HUES: HUES,
    BLENDS: BLENDS,
    COLORS: COLORS
  };
})();
