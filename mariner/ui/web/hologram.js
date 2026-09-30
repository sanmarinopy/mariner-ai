// Mariner — interfaz holográfica.
// Reglas de diseño para ventilador holográfico:
//  * el negro puro NO se proyecta: todo lo que no sea figura va en #000
//  * composición circular centrada (el área visible del ventilador es un disco)
//  * trazos gruesos y brillantes; nada de texto chico
"use strict";

const params = new URLSearchParams(location.search);
const cvs = document.getElementById("holo");
const ctx = cvs.getContext("2d");
const body = document.body;
if (params.get("holo") === "1") body.classList.add("holo");
let mirror = params.get("mirror") === "1";

const STATE_COLORS = {
  idle: [57, 208, 255],
  listening: [61, 255, 154],
  thinking: [255, 176, 0],
  speaking: [127, 232, 255],
};
const ACCENT = [255, 122, 0]; // naranja HUD
const ALERT = [255, 59, 59];

const S = {
  state: "idle",
  color: [...STATE_COLORS.idle],
  level: 0, levelTarget: 0,
  hud: { title: "Sin telemetría", subtitle: "", gauges: [], alerts: [] },
  caption: "", captionKind: "", captionAt: 0,
  heard: "", heardAt: 0,
  connected: false,
};

// ---------------------------------------------------------------- partículas (esfera de Fibonacci)
const N = 520;
const pts = [];
for (let i = 0; i < N; i++) {
  const y = 1 - (i / (N - 1)) * 2;
  const r = Math.sqrt(1 - y * y);
  const th = Math.PI * (3 - Math.sqrt(5)) * i;
  pts.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, seed: Math.random() * 6.283 });
}

// ---------------------------------------------------------------- tamaño
let W = 0, H = 0, DPR = 1;
function resize() {
  DPR = body.classList.contains("holo") ? 1 : Math.min(2, window.devicePixelRatio || 1);
  W = window.innerWidth; H = window.innerHeight;
  cvs.width = W * DPR; cvs.height = H * DPR;
}
window.addEventListener("resize", resize);
resize();

const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
const lerp = (a, b, t) => a + (b - a) * t;

// ---------------------------------------------------------------- dibujo
function draw(t) {
  const time = t / 1000;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  if (mirror) { ctx.translate(W, 0); ctx.scale(-1, 1); }
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, W, H);

  const cx = W / 2, cy = H / 2;
  const R = Math.min(W, H) * 0.47;           // radio del disco visible
  const target = STATE_COLORS[S.state] || STATE_COLORS.idle;
  for (let i = 0; i < 3; i++) S.color[i] = lerp(S.color[i], target[i], 0.08);
  S.level = lerp(S.level, S.levelTarget, 0.35);
  const col = S.color;
  const alerting = S.hud.alerts && S.hud.alerts.length > 0;
  const blink = 0.5 + 0.5 * Math.sin(time * 8);

  drawRing(cx, cy, R, time, col, alerting, blink);
  drawGauges(cx, cy, R, time);
  drawCore(cx, cy, R * 0.34, time, col);
  drawTexts(cx, cy, R, time, col, alerting, blink);

  requestAnimationFrame(draw);
}

function drawRing(cx, cy, R, time, col, alerting, blink) {
  const rr = R * 0.93;
  ctx.lineWidth = 2;
  ctx.strokeStyle = alerting ? rgba(ALERT, 0.35 + 0.55 * blink) : rgba(col, 0.55);
  ctx.beginPath(); ctx.arc(cx, cy, rr, 0, Math.PI * 2); ctx.stroke();
  // marcas giratorias
  const ticks = 72, rot = time * 0.05;
  ctx.strokeStyle = rgba(col, 0.5);
  for (let i = 0; i < ticks; i++) {
    const a = rot + (i / ticks) * Math.PI * 2;
    const long = i % 6 === 0;
    const r1 = rr - (long ? R * 0.045 : R * 0.02);
    ctx.lineWidth = long ? 2.5 : 1.2;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
    ctx.lineTo(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr);
    ctx.stroke();
  }
  // arco de "escaneo" que indica el estado
  const sweep = S.state === "thinking" ? time * 3.2 : time * 0.6;
  const len = S.state === "listening" ? Math.PI * 1.4 : Math.PI * 0.35;
  ctx.lineWidth = R * 0.018;
  ctx.lineCap = "round";
  ctx.strokeStyle = rgba(col, 0.9);
  ctx.beginPath(); ctx.arc(cx, cy, R * 0.86, sweep, sweep + len); ctx.stroke();
  ctx.lineCap = "butt";
}

function drawGauges(cx, cy, R, time) {
  const gs = (S.hud.gauges || []).slice(0, 4);
  const r = R * 0.78, width = R * 0.035;
  // medidores en el semicírculo inferior: izquierda y derecha
  const slots = [
    [Math.PI * 0.62, Math.PI * 0.95],  // abajo-izquierda
    [Math.PI * 0.38, Math.PI * 0.05],  // abajo-derecha (se llena hacia arriba)
    [Math.PI * 1.05, Math.PI * 1.25],
    [Math.PI * 1.95, Math.PI * 1.75],
  ];
  gs.forEach((g, i) => {
    const [a0, a1] = slots[i];
    const ccw = a1 < a0;
    ctx.lineWidth = width;
    ctx.strokeStyle = rgba(ACCENT, 0.18);
    ctx.beginPath(); ctx.arc(cx, cy, r, a0, a1, ccw); ctx.stroke();
    const v = g.value == null ? null : Math.max(0, Math.min(1, g.value));
    if (v != null) {
      const low = v < 0.25;
      const c = low ? ALERT : ACCENT;
      const alpha = low ? 0.6 + 0.4 * Math.sin(time * 6) : 0.95;
      ctx.strokeStyle = rgba(c, alpha);
      ctx.beginPath(); ctx.arc(cx, cy, r, a0, a0 + (a1 - a0) * v, ccw); ctx.stroke();
    }
    // etiqueta
    const am = a0 + (a1 - a0) * 0.88;  // etiqueta junto al extremo superior del arco
    const lx = cx + Math.cos(am) * (r - R * 0.13), ly = cy + Math.sin(am) * (r - R * 0.13);
    ctx.fillStyle = rgba(ACCENT, 0.95);
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = `600 ${Math.round(R * 0.042)}px "Segoe UI", sans-serif`;
    ctx.fillText(g.label, lx, ly - R * 0.03);
    ctx.font = `700 ${Math.round(R * 0.055)}px "Segoe UI", sans-serif`;
    ctx.fillText(v == null ? "--" : `${Math.round(v * 100)}%`, lx, ly + R * 0.03);
  });
}

function drawCore(cx, cy, r, time, col) {
  const lv = S.level;
  const thinking = S.state === "thinking";
  const listening = S.state === "listening";
  // halo
  const halo = ctx.createRadialGradient(cx, cy, r * 0.2, cx, cy, r * 1.9);
  halo.addColorStop(0, rgba(col, 0.16 + lv * 0.25));
  halo.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = halo;
  ctx.beginPath(); ctx.arc(cx, cy, r * 1.9, 0, Math.PI * 2); ctx.fill();

  const ry = time * (thinking ? 1.6 : 0.35), rx = 0.35 + Math.sin(time * 0.3) * 0.15;
  const cyy = Math.cos(ry), syy = Math.sin(ry), cxx = Math.cos(rx), sxx = Math.sin(rx);
  const breathe = 1 + Math.sin(time * 1.6) * 0.025 + (listening ? 0.06 : 0);

  ctx.globalCompositeOperation = "lighter";
  for (const p of pts) {
    // deformación por voz: ondas sobre la esfera
    const wave = lv * 0.22 * Math.sin(p.y * 9 + time * 14 + p.seed) +
                 (thinking ? 0.05 * Math.sin(time * 6 + p.seed * 3) : 0);
    const k = breathe + wave;
    let x = p.x * k, y = p.y * k, z = p.z * k;
    const x1 = x * cyy - z * syy, z1 = x * syy + z * cyy;
    const y2 = y * cxx - z1 * sxx, z2 = y * sxx + z1 * cxx;
    const persp = 1 / (1.9 - z2 * 0.6);
    const sx = cx + x1 * r * persp * 1.6, sy = cy + y2 * r * persp * 1.6;
    const depth = (z2 + 1) / 2;
    const size = 1 + depth * 2.4 + lv * 1.5;
    ctx.fillStyle = rgba(col, 0.18 + depth * 0.75);
    ctx.fillRect(sx - size / 2, sy - size / 2, size, size);
  }
  ctx.globalCompositeOperation = "source-over";

  // órbita inclinada
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(-0.35);
  ctx.strokeStyle = rgba(col, 0.55);
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.ellipse(0, 0, r * 1.45, r * 0.38, 0, 0, Math.PI * 2); ctx.stroke();
  const oa = time * 1.2;
  ctx.fillStyle = rgba(col, 1);
  ctx.beginPath(); ctx.arc(Math.cos(oa) * r * 1.45, Math.sin(oa) * r * 0.38, r * 0.05, 0, Math.PI * 2); ctx.fill();
  ctx.restore();

  // banda de voz
  if (lv > 0.02) {
    ctx.strokeStyle = rgba(col, 0.9);
    ctx.lineWidth = 2;
    ctx.beginPath();
    const w = r * 2.6;
    for (let i = 0; i <= 80; i++) {
      const u = i / 80, xx = cx - w / 2 + u * w;
      const env = Math.sin(u * Math.PI);
      const yy = cy + Math.sin(u * 30 + time * 20) * Math.sin(u * 7 - time * 5) * env * lv * r * 0.35;
      i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy);
    }
    ctx.stroke();
  }
}

function wrap(text, maxW, maxLines) {
  const words = text.split(/\s+/); const lines = []; let cur = "";
  for (const w of words) {
    const test = cur ? cur + " " + w : w;
    if (ctx.measureText(test).width > maxW && cur) { lines.push(cur); cur = w; } else cur = test;
  }
  if (cur) lines.push(cur);
  if (lines.length > maxLines) { const l = lines.slice(-maxLines); l[0] = "… " + l[0]; return l; }
  return lines;
}

function drawTexts(cx, cy, R, time, col, alerting, blink) {
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  // título (sistema) y subtítulo
  ctx.fillStyle = rgba(col, 0.95);
  ctx.font = `700 ${Math.round(R * 0.07)}px "Segoe UI", sans-serif`;
  ctx.fillText((S.hud.title || "").toUpperCase(), cx, cy - R * 0.6);
  ctx.fillStyle = rgba(col, 0.6);
  ctx.font = `500 ${Math.round(R * 0.045)}px "Segoe UI", sans-serif`;
  ctx.fillText(S.hud.subtitle || "", cx, cy - R * 0.51);

  // alerta
  if (alerting) {
    const names = { low_fuel: "COMBUSTIBLE BAJO", overheating: "SOBRECALENTAMIENTO",
      in_danger: "PELIGRO", being_interdicted: "INTERDICCIÓN" };
    ctx.fillStyle = rgba(ALERT, 0.5 + 0.5 * blink);
    ctx.font = `800 ${Math.round(R * 0.05)}px "Segoe UI", sans-serif`;
    ctx.fillText(names[S.hud.alerts[0]] || S.hud.alerts[0].toUpperCase(), cx, cy - R * 0.72);
  }

  // subtítulo de lo que dice el asistente (se desvanece)
  const age = (performance.now() - S.captionAt) / 1000;
  if (S.caption && age < 9) {
    const a = Math.min(1, (9 - age) / 1.5);
    const c = S.captionKind === "callout" ? ACCENT : col;
    ctx.fillStyle = rgba(c, a);
    ctx.font = `600 ${Math.round(R * 0.052)}px "Segoe UI", sans-serif`;
    const lines = wrap(S.caption, R * 1.05, 3);
    lines.forEach((l, i) => ctx.fillText(l, cx, cy + R * 0.5 + i * R * 0.068));
  }
  // lo que escuchó
  const hage = (performance.now() - S.heardAt) / 1000;
  if (S.heard && hage < 5) {
    ctx.fillStyle = rgba(STATE_COLORS.listening, Math.min(0.8, (5 - hage) / 1.5));
    ctx.font = `italic 500 ${Math.round(R * 0.04)}px "Segoe UI", sans-serif`;
    const l = wrap("« " + S.heard + " »", R * 1.2, 1)[0];
    ctx.fillText(l, cx, cy + R * 0.4);
  }
  if (!S.connected) {
    ctx.fillStyle = rgba(ALERT, 0.5 + 0.5 * blink);
    ctx.font = `700 ${Math.round(R * 0.045)}px "Segoe UI", sans-serif`;
    ctx.fillText("SIN ENLACE CON EL NÚCLEO", cx, cy + R * 0.7);
  }
}

// ---------------------------------------------------------------- consola de depuración
const logEl = document.getElementById("log");
function logLine(text, cls) {
  const d = document.createElement("div");
  d.className = cls || ""; d.textContent = text;
  logEl.appendChild(d);
  while (logEl.children.length > 60) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}

// ---------------------------------------------------------------- WebSocket
let ws;
function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { S.connected = true; logLine("Enlace establecido.", "sys"); };
  ws.onclose = () => { S.connected = false; setTimeout(connect, 1500); };
  ws.onmessage = (m) => {
    const { topic, data } = JSON.parse(m.data);
    switch (topic) {
      case "assistant.state": S.state = data.state; break;
      case "audio.level": S.levelTarget = data.level; break;
      case "game.hud": S.hud = data; break;
      case "assistant.say":
        S.caption = data.text; S.captionKind = data.kind; S.captionAt = performance.now();
        logLine(data.text, data.kind === "callout" ? "callout" : "");
        break;
      case "user.said":
        S.heard = data.text; S.heardAt = performance.now();
        logLine((data.speaker ? `[${data.speaker}] ` : "Tú: ") + data.text, "you");
        break;
    }
  };
}
connect();

document.getElementById("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = document.getElementById("q");
  if (q.value.trim() && ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "say", text: q.value.trim() }));
  q.value = "";
});

window.addEventListener("keydown", (e) => {
  if (document.activeElement && document.activeElement.id === "q") {
    if (e.key === "Escape") document.activeElement.blur();
    return;
  }
  const k = e.key.toLowerCase();
  if (k === "h") { body.classList.toggle("holo"); resize(); }
  if (k === "f") { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen(); }
  if (k === "m") mirror = !mirror;
});

requestAnimationFrame(draw);
