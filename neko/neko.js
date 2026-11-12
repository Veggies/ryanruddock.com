/* ============================================================
   NEKO  ::  the 1980s desktop cat, chasing the mouse around the
   outside of the page. Sprites: neko/LICENSE.txt.

   The cat lives on a layer *behind* .page, in the starfield. When the
   mouse is over the page it heads for the nearest point just outside
   the page's edge; when it has to cross, it runs behind the page and
   comes out the other side. Stopped against the edge, it claws at it,
   the way the original clawed at window borders.

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

  /* ============ geometry: the page's edge, grown by half a cat ============ */

  function zone() {
    var r = page.getBoundingClientRect();
    return { left: r.left - HALF - GAP, right: r.right + HALF + GAP, top: r.top - HALF - GAP, bottom: r.bottom + HALF + GAP };
  }

  function inside(z, x, y) {
    return x > z.left && x < z.right && y > z.top && y < z.bottom;
  }

  /* the nearest point just outside the zone, for a point inside it */
  function outside(z, x, y) {
    if (!inside(z, x, y)) { return { x: x, y: y, side: null }; }
    var options = [
      { d: x - z.left, x: z.left, y: y, side: "left" },
      { d: z.right - x, x: z.right, y: y, side: "right" },
      { d: y - z.top, x: x, y: z.top, side: "top" },
      { d: z.bottom - y, x: x, y: z.bottom, side: "bottom" }
    ];
    // only sides with room on screen for a cat
    var vw = innerWidth, vh = innerHeight;
    options = options.filter(function (o) {
      return o.x >= HALF && o.x <= vw - HALF && o.y >= HALF && o.y <= vh - HALF;
    });
    if (!options.length) { return null; }
    options.sort(function (a, b) { return a.d - b.d; });
    return options[0];
  }

  function clampToScreen(p) {
    return {
      x: Math.min(innerWidth - HALF, Math.max(HALF, p.x)),
      y: Math.min(innerHeight - HALF, Math.max(HALF, p.y)),
      side: p.side
    };
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
    var target = mouse ? outside(z, mouse.x, mouse.y) : { x: x, y: y };
    if (!target) { target = { x: x, y: y }; }
    target = clampToScreen(target);

    var dx = target.x - x, dy = target.y - y;
    var dist = Math.sqrt(dx * dx + dy * dy);

    if (dist > NEAR) {
      // woken from a nap or an activity: startled for a moment first
      if (idle >= 6) { alert = 3; }
      idle = 0;
      activity = null;
      if (alert > 0) {
        alert--;
        show("awake");
      } else {
        var step = Math.min(STEP, dist);
        x += dx / dist * step;
        y += dy / dist * step;
        show(heading(dx, dy) + (frame % 2 ? "1" : "2"));
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
