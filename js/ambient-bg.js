/**
 * Лёгкий анимированный фон (canvas). Эксперимент: при необходимости отключите
 * скрипт в base.html или удалите файл.
 */
(function () {
  const canvas = document.getElementById("ambient-canvas");
  if (!canvas || !canvas.getContext) return;

  const ctx = canvas.getContext("2d");
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const t0 = performance.now();

  const blobs = [
    { bx: 0.14, by: 0.32, br: 0.52, ax: 1, ay: 0.65, spd: 0.085 },
    { bx: 0.82, by: 0.48, br: 0.46, ax: -0.75, ay: 0.55, spd: 0.068 },
    { bx: 0.42, by: 0.82, br: 0.4, ax: 0.55, ay: -0.85, spd: 0.075 },
  ];

  let W = 0;
  let H = 0;
  let dpr = 1;

  function syncSize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawBlobs(timeFactor) {
    ctx.clearRect(0, 0, W, H);
    blobs.forEach((b, i) => {
      const ox = Math.sin(timeFactor * b.spd * 2 + i) * b.ax * 0.042 * W;
      const oy = Math.cos(timeFactor * b.spd * 1.65 + i * 0.75) * b.ay * 0.042 * H;
      const cx = b.bx * W + ox;
      const cy = b.by * H + oy;
      const rad = Math.min(W, H) * b.br;
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
      const hue = 232 + i * 20;
      g.addColorStop(0, "hsla(" + hue + ", 70%, 55%, 0.14)");
      g.addColorStop(0.42, "hsla(" + hue + ", 65%, 50%, 0.055)");
      g.addColorStop(1, "transparent");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    });
  }

  function paintStatic() {
    drawBlobs(0);
  }

  function tick(now) {
    drawBlobs((now - t0) * 0.001);
    if (!reduced) requestAnimationFrame(tick);
  }

  syncSize();

  window.addEventListener(
    "resize",
    function () {
      syncSize();
      if (reduced) paintStatic();
    },
    { passive: true },
  );

  if (reduced) {
    paintStatic();
  } else {
    requestAnimationFrame(tick);
  }
})();
