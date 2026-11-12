/* ============================================================
   NEKO  ::  the 1980s desktop cat, chasing the mouse around the
   outside of the page. Sprites: neko/LICENSE.txt.

   The page's gold border is a wall. The cat lives in the starfield
   around it and never crosses the page: to reach the other side it
   walks around a corner, choosing the shorter way among the corners
   that are on screen. When none are -- the page is taller than the
   window -- the other side is out of reach, so it waits on its own
   side at the mouse's height and claws at the border, the way the
   original clawed at window edges.

   Only for a fine pointer that can hover, and not with reduced motion.
   ============================================================ */

(function () {
  "use strict";

  if (!window.matchMedia || !matchMedia("(hover: hover) and (pointer: fine)").matches) { return; }
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { return; }

  var page = document.querySelector(".page");
  if (!page) { return; }

  var BASE = document.currentScript
    ? document.currentScript.src.replace(/[^/]*$/, "")
    : "./neko/";

  var SIZE = 32;      // sprite size, px
  var STEP = 18;      // px per tick while running
  var TICK = 110;     // ms per tick
  var GAP = 4;        // how far outside the page edge the cat stops
  var NEAR = 20;      // close enough to the target to stop
  var HALF = SIZE / 2;

  var SPRITES = ("awake down1 down2 downclaw1 downclaw2 downleft1 downleft2 downright1 downright2 " +
    "left1 left2 leftclaw1 leftclaw2 right1 right2 rightclaw1 rightclaw2 scratch1 scratch2 " +
    "sleep1 sleep2 up1 up2 upclaw1 upclaw2 upleft1 upleft2 upright1 upright2 wash1 yawn1 yawn2").split(" ");
  SPRITES.forEach(function (name) { new Image().src = BASE + name + ".png"; });   // preload

  // the page sits above the cat
  if (getComputedStyle(page).position === "static") { page.style.position = "relative"; }
  page.style.zIndex = "1";

  var cat = document.createElement("div");
  cat.setAttribute("aria-hidden", "true");
  cat.style.cssText = "position:fixed;left:0;top:0;width:" + SIZE + "px;height:" + SIZE + "px;" +
    "z-index:0;pointer-events:none;image-rendering:pixelated;background-size:100% 100%;" +
    "will-change:transform;";
  document.body.appendChild(cat);

  /* ============ geometry: the page's border is a wall ============ */

  /* the page grown by half a cat: the cat's centre stays outside this */
  function zone() {
    var r = page.getBoundingClientRect();
    return { left: r.left - HALF - GAP, right: r.right + HALF + GAP, top: r.top - HALF - GAP, bottom: r.bottom + HALF + GAP };
  }

  function onScreen(p) {
    return p.x >= HALF && p.x <= innerWidth - HALF && p.y >= HALF && p.y <= innerHeight - HALF;
  }

  function clampToScreen(p) {
    return { x: Math.min(innerWidth - HALF, Math.max(HALF, p.x)), y: Math.min(innerHeight - HALF, Math.max(HALF, p.y)) };
  }

  /* Does a straight run from a to b go through the page? Liang-Barsky
     clipping against the zone shrunk by a hair, so running along an edge
     or touching a corner does not count. */
  function crosses(z, a, b) {
    var e = 0.5;
    var dx = b.x - a.x, dy = b.y - a.y;
    var p = [-dx, dx, -dy, dy];
    var q = [a.x - (z.left + e), (z.right - e) - a.x, a.y - (z.top + e), (z.bottom - e) - a.y];
    var t0 = 0, t1 = 1;
    for (var i = 0; i < 4; i++) {
      if (p[i] === 0) {
        if (q[i] < 0) { return false; }          // parallel to this edge and outside it
      } else {
        var t = q[i] / p[i];
        if (p[i] < 0) { if (t > t1) { return false; } if (t > t0) { t0 = t; } }
        else { if (t < t0) { return false; } if (t < t1) { t1 = t; } }
      }
    }
    return t0 < t1;
  }

  /* a point inside the zone, moved to the nearest edge that is on screen */
  function pushOut(z, p) {
    if (!(p.x > z.left && p.x < z.right && p.y > z.top && p.y < z.bottom)) { return p; }
    var options = [
      { x: z.left, y: p.y, d: p.x - z.left },
      { x: z.right, y: p.y, d: z.right - p.x },
      { x: p.x, y: z.top, d: p.y - z.top },
      { x: p.x, y: z.bottom, d: z.bottom - p.y }
    ].filter(onScreen);
    options.sort(function (a, b) { return a.d - b.d; });
    return options[0] || null;
  }

  /* The next place to run to on the way from `from` to `to`, going around
     the page by its on-screen corners; null when there is no way round.
     Shortest path over at most six points, so a plain O(n^2) Dijkstra. */
  function nextStep(z, from, to) {
    if (!crosses(z, from, to)) { return to; }
    var corners = [
      { x: z.left - 1, y: z.top - 1 }, { x: z.right + 1, y: z.top - 1 },
      { x: z.left - 1, y: z.bottom + 1 }, { x: z.right + 1, y: z.bottom + 1 }
    ].filter(onScreen);
    var nodes = [from].concat(corners, [to]);
    var n = nodes.length, goal = n - 1;
    var dist = [], prev = [], done = [];
    for (var i = 0; i < n; i++) { dist.push(Infinity); prev.push(-1); done.push(false); }
    dist[0] = 0;
    for (var k = 0; k < n; k++) {
      var u = -1;
      for (var j = 0; j < n; j++) { if (!done[j] && (u < 0 || dist[j] < dist[u])) { u = j; } }
      if (u < 0 || dist[u] === Infinity) { break; }
      done[u] = true;
      for (var v = 0; v < n; v++) {
        if (done[v] || crosses(z, nodes[u], nodes[v])) { continue; }
        var d = dist[u] + Math.hypot(nodes[v].x - nodes[u].x, nodes[v].y - nodes[u].y);
        if (d < dist[v]) { dist[v] = d; prev[v] = u; }
      }
    }
    if (dist[goal] === Infinity) { return null; }
    var step = goal;
    while (prev[step] !== 0) { step = prev[step]; }
    return nodes[step];
  }

  /* Out of reach: wait on the cat's own side, level with the target. */
  function ownSide(z, cx, cy, to) {
    if (cx <= z.left) { return { x: z.left, y: to.y }; }
    if (cx >= z.right) { return { x: z.right, y: to.y }; }
    if (cy <= z.top) { return { x: to.x, y: z.top }; }
    return { x: to.x, y: z.bottom };
  }

  /* no room beside the page (a narrow window): no cat */
  function roomy() {
    var r = page.getBoundingClientRect();
    return r.left >= SIZE + GAP * 2 || innerWidth - r.right >= SIZE + GAP * 2;
  }

  /* ============ state ============ */

  var z0 = zone();
  var x = Math.max(HALF + 4, z0.left - 20);        // start asleep in the left margin
  var y = Math.min(innerHeight - HALF - 4, 140);
  var mouse = null;                                // nothing to chase until the mouse moves
  var idle = 60;                                   // ...so it begins asleep
  var alert = 0;
  var activity = null;
  var frame = 0;

  document.addEventListener("mousemove", function (ev) {
    mouse = { x: ev.clientX, y: ev.clientY };
  }, { passive: true });

  function show(name) {
    cat.style.backgroundImage = "url(" + BASE + name + ".png)";
  }

  function place() {
    cat.style.transform = "translate(" + Math.round(x - HALF) + "px," + Math.round(y - HALF) + "px)";
  }

  /* 8 compass directions, matching the sprite names */
  function heading(dx, dy) {
    var a = Math.atan2(dy, dx) * 180 / Math.PI;   // 0 = right, 90 = down
    var names = ["right", "downright", "down", "downleft", "left", "upleft", "up", "upright"];
    return names[(Math.round(a / 45) + 8) % 8];
  }

  /* which way the page's edge is from a resting cat, for clawing at it */
  function wall(z) {
    var e = 3;
    if (Math.abs(x - z.left) < e) { return "rightclaw"; }    // page is to its right
    if (Math.abs(x - z.right) < e) { return "leftclaw"; }
    if (Math.abs(y - z.top) < e) { return "downclaw"; }
    if (Math.abs(y - z.bottom) < e) { return "upclaw"; }
    return null;
  }

  function restingSprite(z) {
    if (idle === 6) {
      var clawAt = wall(z);
      var pool = ["wash", "scratch"];
      if (clawAt) { pool.push(clawAt, clawAt); }    // clawing is the classic, so twice as likely
      activity = pool[Math.floor(Math.random() * pool.length)];
    }
    if (idle >= 6 && idle < 18 && activity) {
      if (activity === "wash") { return frame % 4 < 2 ? "wash1" : "yawn1"; }
      return activity + (frame % 2 ? "1" : "2");
    }
    if (idle < 30) { return "yawn1"; }               // sitting
    if (idle < 34) { return "yawn2"; }               // a big yawn...
    return frame % 8 < 4 ? "sleep1" : "sleep2";      // ...then asleep
  }

  function tick() {
    frame++;
    if (!roomy()) { cat.style.display = "none"; return; }
    cat.style.display = "";

    var z = zone();

    // scrolling can slide the page over a resting cat: step back out
    var here = pushOut(z, { x: x, y: y });
    if (!here) { cat.style.display = "none"; return; }
    x = here.x;
    y = here.y;

    var target = mouse ? pushOut(z, clampToScreen(mouse)) : null;
    if (!target) { target = { x: x, y: y }; }

    var way = nextStep(z, { x: x, y: y }, target);
    if (!way) {
      target = clampToScreen(ownSide(z, x, y, target));
      way = nextStep(z, { x: x, y: y }, target) || target;
    }

    var dist = Math.hypot(target.x - x, target.y - y);

    if (dist > NEAR) {
      // woken from a nap or an activity: startled for a moment first
      if (idle >= 6) { alert = 3; }
      idle = 0;
      activity = null;
      if (alert > 0) {
        alert--;
        show("awake");
      } else {
        // toward the next corner (or the target itself when nothing is in the way)
        var dx = way.x - x, dy = way.y - y;
        var leg = Math.hypot(dx, dy);
        if (leg > 0) {
          var step = Math.min(STEP, leg);
          x += dx / leg * step;
          y += dy / leg * step;
          show(heading(dx, dy) + (frame % 2 ? "1" : "2"));
        }
      }
    } else {
      // settle exactly onto the target so clawing lines up with the edge
      x = target.x;
      y = target.y;
      idle++;
      show(restingSprite(z));
    }
    place();
  }

  show("sleep1");
  place();
  setInterval(tick, TICK);
})();
