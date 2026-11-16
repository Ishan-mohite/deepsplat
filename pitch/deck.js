/*
 * deepsplat jury pitch: slide engine, 3D point-cloud scenes and per-slide animation.
 *
 * Keys: → Space PageDown = next · ← PageUp = back · F fullscreen · S speaker notes · T timer (R resets)
 *       B black screen · P pause the demo video · 1-8 jump to a slide
 * URL:  ?instant shows every state without animation. ?export=<slide>&layer=<id> and ?manifest are used
 *       by tools/build_pptx.py to turn the slides into the .pptx backup.
 */
(function () {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const params = new URLSearchParams(location.search);
  const EXPORT = params.has('export');
  const MANIFEST = params.has('manifest');
  const INSTANT = EXPORT || MANIFEST || params.has('instant');
  const root = document.documentElement;
  if (INSTANT) root.classList.add('instant');
  if (EXPORT) root.classList.add('export');

  /* The video's paper tone after H.264 decoding; the page takes it on while the video plays, so there is no seam. */
  const VIDEO_PAPER = '#F1EDE5';
  const TARGET_SECONDS = 240;

  /* Speaker notes: "▸" marks a click. Times are targets for a 4-minute pitch. */
  const NOTES = {
    title: { name: 'Title', time: '0:00 – 0:10',
      say: 'Hi, we are deepsplat. This weekend we built a 3D damage map: scan a site once, and it flags what has changed.' },
    problem: { name: 'Problem', time: '0:10 – 0:50',
      say: 'Today a crack is just a photo on someone’s phone. ▸ A month later nobody can say where exactly it was, whether it is new, or whether it is growing. ▸ And inside buildings, tunnels and car parks there is no GPS to help. ▸ Why it matters: safety, because cracks are the early sign of structural damage. Cost, because fixed early a crack is a small repair, not a failure. And proof: owners, insurers and authorities need records they can compare over time.' },
    idea: { name: 'Idea', time: '0:50 – 1:12',
      say: 'Our idea: scan the site once into a photoreal 3D twin. ▸ From then on any camera, a phone today and a robot tomorrow, finds its own position inside that twin, without GPS. ▸ We detect the damage, pin it to its exact spot, measure it, and compare it with every earlier record of that spot.' },
    demo: { name: 'Demo video (58 s, moves on by itself)', time: '1:12 – 2:10',
      say: 'This is the real pipeline, recorded in the conference hall here in Leukerbad. One: a handheld scan becomes a Gaussian splat of the room. Two: an inspector spots damage. We taped paper cracks to the walls. Three: a ten-second sweep and one photo in our iPhone app. Four: upload. — Then it lands in the dashboard: two open issues on the floor map. In the 3D twin the crack sits exactly where it was photographed. SAM 3 found it, our own visual positioning placed it, and it measures 55 centimetres.' },
    change: { name: 'Flag what has changed', time: '2:10 – 2:36',
      say: 'And this is the challenge: flag what has changed. ▸ The next photo of this spot is matched to the same crack, within fifteen centimetres. If it is twenty percent longer, it is flagged as worsening. If it is gone, the map suggests repaired and a person confirms. A time slider shows the site on any date, and every issue goes into a PDF report.' },
    proof: { name: 'Proof', time: '2:36 – 2:54',
      say: 'Everything you saw runs end to end: an iPhone app, a backend with our own visual positioning and SAM 3, a 3D viewer and reports. On held-out views of the scan, positioning is accurate to six millimetres, with zero wrong poses.' },
    next: { name: 'What’s next (drone clip 10 s)', time: '2:54 – 3:36',
      say: 'Next: more damage types and more sites: tunnels, car parks, bridges. ▸ And robots: drones, robot dogs, humanoids. From the same scan we export a map a ground robot can navigate with. ▸ Here a drone lands inside our scan, in simulation: the elevation map gives it its reward and a safe altitude. So the same twin serves inspection today, navigation and training robot policies next. Robots patrol, the map updates itself, people only confirm.' },
    team: { name: 'Team and close', time: '3:36 – 3:55',
      say: 'We are six people from six countries, studying robotics, AI, mechatronics and electrical engineering at ETH, TUHH, Freiberg and USI. ▸ deepsplat: one map, every damage record in its exact place. Thank you.' },
  };

  /* ------------------------------------------------------------------ helpers */
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const easeOut = (p) => 1 - Math.pow(1 - p, 3);
  const easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
  function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function tween(obj, key, to, ms, ease, done) {
    obj.__tw = obj.__tw || {};
    if (obj.__tw[key]) cancelAnimationFrame(obj.__tw[key]);
    if (INSTANT || ms <= 0) { obj[key] = to; if (done) done(); return; }
    const from = obj[key], t0 = performance.now();
    const tick = (now) => {
      const p = (now - t0) / ms;
      if (p >= 1) { obj[key] = to; obj.__tw[key] = 0; if (done) done(); return; }
      obj[key] = from + (to - from) * (ease ? ease(p) : p);
      obj.__tw[key] = requestAnimationFrame(tick);
    };
    obj.__tw[key] = requestAnimationFrame(tick);
  }
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const mul = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
  const lerp3 = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const norm = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

  /* ------------------------------------------------------------------ point cloud of the hall (real scan data) */
  const CLOUD = (function () {
    const src = window.HALL_CLOUD, n = src.n, raw = atob(src.b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const dv = new DataView(bytes.buffer);
    const pos = new Float32Array(n * 3), off = new Float32Array(n * 3), dl = new Float32Array(n), trav = new Uint8Array(n);
    const rgbaH = new Uint8Array(n * 4), rgbaT = new Uint8Array(n * 4);
    const rnd = rng(11);
    const PAPER = [241, 238, 230], INK = [23, 32, 51], BLUE = [36, 83, 212], GREEN = [47, 125, 75], GREY = [74, 81, 99];
    const mix = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
    const put = (arr, i, c) => { arr[i * 4] = Math.round(c[0]); arr[i * 4 + 1] = Math.round(c[1]); arr[i * 4 + 2] = Math.round(c[2]); arr[i * 4 + 3] = 255; };
    let tx = 0, ty = 0, tn = 0;
    for (let i = 0; i < n; i++) {
      const o = i * 6, x = dv.getInt16(o, true) / 200, y = dv.getInt16(o + 2, true) / 200, z = dv.getUint8(o + 4) / 100 - 0.12;
      pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = z; trav[i] = dv.getUint8(o + 5) & 1;
      const a = rnd() * 6.2832, r = 4 + rnd() * 10;                 // where the point starts when the cloud assembles
      off[i * 3] = Math.cos(a) * r + x * 0.8; off[i * 3 + 1] = Math.sin(a) * r + y * 0.8; off[i * 3 + 2] = 1.5 + rnd() * 8; dl[i] = rnd() * 0.5;
      let k = clamp(z / 2.3, 0, 1); k = k * k * (3 - 2 * k);
      const grain = rnd() * 0.22;
      put(rgbaH, i, mix(mix(INK, BLUE, 0.3 + 0.7 * k), PAPER, grain));                                   // by height
      put(rgbaT, i, trav[i] ? mix(GREEN, PAPER, grain * 0.6) : mix(mix(GREY, PAPER, 0.62), PAPER, grain * 0.5));   // drivable or not
      if (trav[i]) { tx += x; ty += y; tn++; }
    }
    tx /= tn; ty /= tn;
    let sx = 0, sy = 0;
    for (let i = 0; i < n; i++) if (trav[i]) { sx += (pos[i * 3] - tx) ** 2; sy += (pos[i * 3 + 1] - ty) ** 2; }
    function nearest(px, py) {
      let best = 0, bd = 1e9;
      for (let i = 0; i < n; i++) { const dx = pos[i * 3] - px, dy = pos[i * 3 + 1] - py, d = dx * dx + dy * dy; if (d < bd) { bd = d; best = i; } }
      return [pos[best * 3], pos[best * 3 + 1], pos[best * 3 + 2]];
    }
    return { n, pos, off, dl, rgbaH, rgbaT, cH: new Uint32Array(rgbaH.buffer), cT: new Uint32Array(rgbaT.buffer), nearest,
      floor: { x: tx, y: ty, rx: Math.sqrt(sx / tn), ry: Math.sqrt(sy / tn) } };
  })();

  const VS = `
    attribute vec3 aPos; attribute vec3 aOff; attribute float aDl; attribute vec4 aCol; attribute vec4 aCol2;
    uniform float uAz, uTilt, uS, uR, uP, uMix, uPs; uniform vec2 uCenter, uScale;
    varying vec4 vCol;
    void main() {
      vec3 p = aPos; float vis = 1.0, a = 1.0;
      if (uP < 1.0) { float q = (uP - aDl) * 2.0; if (q <= 0.0) vis = 0.0; else if (q < 1.0) { p += aOff * pow(1.0 - q, 3.0); a = q * q; } }
      float ca = cos(uAz), sa = sin(uAz), ce = cos(uTilt), se = sin(uTilt);
      float xr = p.x * ca + p.y * sa, yr = -p.x * sa + p.y * ca;
      float d = yr * ce - p.z * se, f = uR / (uR + d);
      gl_Position = vec4(uCenter + vec2(xr, yr * se + p.z * ce) * uS * f * uScale, clamp(d / 60.0, -0.99, 0.99), 1.0);
      gl_PointSize = vis * uPs * (0.8 + 0.5 * clamp((f - 0.8) / 0.6, 0.0, 1.0));
      vec4 c = mix(aCol, aCol2, uMix);
      vCol = vec4(c.rgb * a, a);                       // premultiplied: points fade in while they fly to their place
    }`;
  const FS = `
    precision mediump float; varying vec4 vCol;
    void main() { vec2 c = gl_PointCoord - 0.5; if (dot(c, c) > 0.25) discard; gl_FragColor = vCol; }`;

  /* A scene draws the cloud with WebGL (smooth on any laptop). Without WebGL, and for the still exports, it falls back to a 2D canvas. */
  class Scene {
    constructor(el, opt) {
      this.el = el; this.cv = $('canvas', el);
      Object.assign(this, { az: -1.25, tilt: 0.6, sway: 0.3, period: 22, S: 56, R: 34, cx: 0.5, cy: 0.56, assemble: 1, mix: 0, running: false, t0: 0, cur: 0 }, opt);
      this.cur = this.az;
      if (!INSTANT) this.initGL();
      if (!this.gl) this.ctx = this.cv.getContext('2d');
      this.resize();
    }
    initGL() {
      let gl = null;
      try {
        gl = this.cv.getContext('webgl', { alpha: true, antialias: false, depth: true, premultipliedAlpha: true });
        if (!gl) { console.warn('deepsplat deck: no WebGL context, drawing the point cloud on a 2D canvas instead.'); return; }
        const C = CLOUD, prog = gl.createProgram();
        const shader = (type, src) => { const sh = gl.createShader(type); gl.shaderSource(sh, src); gl.compileShader(sh); if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh)); return sh; };
        gl.attachShader(prog, shader(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
        gl.useProgram(prog);
        const attr = (data, name, size, type, norm) => {
          const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
          const loc = gl.getAttribLocation(prog, name); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, type, !!norm, 0, 0);
        };
        attr(C.pos, 'aPos', 3, gl.FLOAT); attr(C.off, 'aOff', 3, gl.FLOAT); attr(C.dl, 'aDl', 1, gl.FLOAT);
        attr(C.rgbaH, 'aCol', 4, gl.UNSIGNED_BYTE, true); attr(C.rgbaT, 'aCol2', 4, gl.UNSIGNED_BYTE, true);
        this.U = {};
        ['uAz', 'uTilt', 'uS', 'uR', 'uP', 'uMix', 'uPs', 'uCenter', 'uScale'].forEach((k) => { this.U[k] = gl.getUniformLocation(prog, k); });
        gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LESS); gl.clearColor(0, 0, 0, 0);
        gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        this.gl = gl;
      } catch (e) {
        console.warn('deepsplat deck: WebGL not usable, drawing the point cloud on a 2D canvas instead.', e);
        if (gl) { const fresh = this.cv.cloneNode(false); this.cv.replaceWith(fresh); this.cv = fresh; }   // a canvas keeps its first context type
        this.gl = null;
      }
    }
    resize() {
      const w = this.el.offsetWidth, h = this.el.offsetHeight, dev = (window.__k || 1) * (window.devicePixelRatio || 1);
      this.q = EXPORT ? 2 : this.gl ? clamp(dev, 1, 3) : 1;
      this.w = w; this.h = h; this.W = Math.round(w * this.q); this.H = Math.round(h * this.q);
      this.cv.width = this.W; this.cv.height = this.H;
      if (!this.gl) {
        this.img = this.ctx.createImageData(this.W, this.H);
        this.buf = new Uint32Array(this.img.data.buffer); this.zb = new Float32Array(this.W * this.H);
      }
      if (INSTANT && this.t0) this.draw(performance.now());   // resizing clears the canvas; no loop runs in instant mode
    }
    /* World (metres, z up) to scene pixels. The camera orbits the hall at azimuth `cur`, looking down by `tilt`. */
    project(x, y, z) {
      const ca = Math.cos(this.cur), sa = Math.sin(this.cur), ce = Math.cos(this.tilt), se = Math.sin(this.tilt);
      const xr = x * ca + y * sa, yr = -x * sa + y * ca, d = yr * ce - z * se, f = this.R / (this.R + d);
      return { u: this.cx * this.w + xr * this.S * f, v: this.cy * this.h - (yr * se + z * ce) * this.S * f, f, d };
    }
    draw(now) {
      this.cur = this.az + (this.sway && !INSTANT ? Math.sin(((now - this.t0) / 1000) * 6.2832 / this.period) * this.sway : 0);
      if (this.gl) this.drawGL(); else this.draw2D();
      if (this.onframe) this.onframe(this, now);
    }
    drawGL() {
      const gl = this.gl, U = this.U;
      gl.viewport(0, 0, this.W, this.H); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.uniform1f(U.uAz, this.cur); gl.uniform1f(U.uTilt, this.tilt); gl.uniform1f(U.uS, this.S * this.q); gl.uniform1f(U.uR, this.R);
      gl.uniform1f(U.uP, this.assemble); gl.uniform1f(U.uMix, this.mix); gl.uniform1f(U.uPs, Math.max(2, 2.7 * this.q));
      gl.uniform2f(U.uCenter, this.cx * 2 - 1, 1 - this.cy * 2); gl.uniform2f(U.uScale, 2 / this.W, 2 / this.H);
      gl.drawArrays(gl.POINTS, 0, CLOUD.n);
    }
    draw2D() {
      const C = CLOUD, W = this.W, H = this.H, buf = this.buf, zb = this.zb, n = C.n, P = C.pos, O = C.off;
      buf.fill(0); zb.fill(1e9);
      const ca = Math.cos(this.cur), sa = Math.sin(this.cur), ce = Math.cos(this.tilt), se = Math.sin(this.tilt);
      const S = this.S * this.q, R = this.R, cx = this.cx * W, cy = this.cy * H, p = this.assemble, m = this.mix;
      const ps0 = Math.max(2, Math.round(2 * this.q)), cH = C.cH, cT = C.cT;
      for (let i = 0; i < n; i++) {
        let x = P[i * 3], y = P[i * 3 + 1], z = P[i * 3 + 2];
        if (p < 1) {
          const q = (p - C.dl[i]) * 2;
          if (q <= 0) continue;
          if (q < 1) { const k = Math.pow(1 - q, 3); x += O[i * 3] * k; y += O[i * 3 + 1] * k; z += O[i * 3 + 2] * k; }
        }
        const xr = x * ca + y * sa, yr = -x * sa + y * ca, d = yr * ce - z * se, f = R / (R + d);
        const u = (cx + xr * S * f) | 0, v = (cy - (yr * se + z * ce) * S * f) | 0;
        const ps = (f - 0.88) * 2.2 > C.dl[i] * 2 ? ps0 + 1 : ps0;   // nearer points are a little larger, dithered
        if (u < 0 || v < 0 || u > W - ps || v > H - ps) continue;
        let c;
        if (m <= 0) c = cH[i]; else if (m >= 1) c = cT[i];
        else {
          const a = cH[i], b = cT[i];
          const r = (a & 255) + ((b & 255) - (a & 255)) * m, g = ((a >> 8) & 255) + (((b >> 8) & 255) - ((a >> 8) & 255)) * m, bl = ((a >> 16) & 255) + (((b >> 16) & 255) - ((a >> 16) & 255)) * m;
          c = ((255 << 24) | (bl << 16) | (g << 8) | r) >>> 0;
        }
        for (let dy = 0; dy < ps; dy++) {
          let k = (v + dy) * W + u;
          for (let dx = 0; dx < ps; dx++, k++) if (d < zb[k]) { zb[k] = d; buf[k] = c; }
        }
      }
      this.ctx.putImageData(this.img, 0, 0);
    }
    start() {
      this.t0 = performance.now();
      if (INSTANT) { this.draw(this.t0); return; }
      if (this.running) return;
      this.running = true;
      const loop = (now) => { if (!this.running) return; this.draw(now); this.raf = requestAnimationFrame(loop); };
      this.raf = requestAnimationFrame(loop);
    }
    stop() { this.running = false; cancelAnimationFrame(this.raf); }
    redraw() { if (INSTANT) this.draw(performance.now()); }
  }
  const place = (el, p) => { el.style.transform = 'translate(' + p.u.toFixed(1) + 'px,' + p.v.toFixed(1) + 'px)'; };
  const scenes = {};
  const getScene = (id, opt) => scenes[id] || (scenes[id] = new Scene($('#' + id), opt));
  const stopScene = (id) => { if (scenes[id]) scenes[id].stop(); };

  /* Where the real crack record sits in the hall (on the tiers), and where the inspector stands. */
  const CRACK = (() => { const p = CLOUD.nearest(3.6, 3.0); return [p[0], p[1], p[2] + 0.1]; })();
  const PHONE = [-4.1, 0.4, 1.5];
  const AIM_FROM = (() => { const p = CLOUD.nearest(5.2, -2.6); return [p[0], p[1], p[2] + 0.4]; })();

  /* ------------------------------------------------------------------ slide engine */
  const slides = $$('.slide');
  const hooks = {};
  let cur = 0, step = 0, lockUntil = 0, started = null;
  const stepsOf = (i) => +slides[i].dataset.steps || 0;
  const stepOf = (el) => { const h = el.closest('[data-step]'); return h ? +h.dataset.step : 0; };
  const animated = (s) => $$('[data-a], .reveal, .draw', s);

  function paint(s, st) {
    s.dataset.at = st;
    animated(s).forEach((el) => el.classList.toggle('on', stepOf(el) <= st));
  }
  function show(i, st, force) {
    const prev = slides[cur], s = slides[i], h = hooks[s.id] || {};
    if (i !== cur || force) {
      const ph = hooks[prev.id];
      if (ph && ph.leave && prev !== s) ph.leave();
      prev.classList.remove('active');
      s.classList.add('noanim');
      animated(s).forEach((el) => el.classList.remove('on'));
      void s.offsetWidth;                       // commit the hidden state, then animate from it
      s.classList.remove('noanim');
      s.classList.add('active');
      cur = i; step = st;
      paint(s, st);
      if (h.enter) h.enter(st);
      for (let k = 1; k <= st; k++) if (h.step) h.step(k, true);
    } else if (st !== step) {
      const was = step; step = st; paint(s, st);
      if (st > was) { for (let k = was + 1; k <= st; k++) if (h.step) h.step(k, false); }
      else if (h.back) h.back(st);
    }
    const chrome = $('#chrome');
    chrome.dataset.mode = s.dataset.chrome || 'full';
    $('#pg').textContent = String(cur + 1).padStart(2, '0');
    $('#track b').style.width = ((cur + (stepsOf(cur) ? step / (stepsOf(cur) + 1) : 0) + 1) / slides.length * 100).toFixed(2) + '%';
    const n = NOTES[s.id];
    if (n) { $('#n-title').textContent = (cur + 1) + ' · ' + n.name; $('#n-time').textContent = n.time; $('#n-say').textContent = n.say; }
    if (!INSTANT) history.replaceState(null, '', '#' + (cur + 1) + (step ? '.' + step : ''));
  }
  function next(auto) {
    if (!auto && performance.now() < lockUntil) return;
    if (started === null) started = performance.now();
    if (step < stepsOf(cur)) show(cur, step + 1);
    else if (cur < slides.length - 1) show(cur + 1, 0);
  }
  function prev() {
    if (step > 0) show(cur, step - 1);
    else if (cur > 0) show(cur - 1, stepsOf(cur - 1));
  }

  /* ------------------------------------------------------------------ per-slide behaviour */
  hooks.title = {
    enter() {
      const s = getScene('sc-title', { S: 43, cx: 0.732, cy: 0.553, az: -1.22, sway: 0.26 });
      const pin = $('#pin-title');
      s.onframe = () => place(pin, s.project(CRACK[0], CRACK[1], CRACK[2]));
      s.assemble = 0; s.mix = 0; s.start();
      tween(s, 'assemble', 1, 3000, null, () => s.redraw());
      s.redraw();
    },
    leave() { stopScene('sc-title'); },
  };

  const idea = { aim: 0 };
  hooks.idea = {
    enter() {
      const s = getScene('sc-idea', { S: 48, cx: 0.678, cy: 0.648, az: -1.05, sway: 0.2 });
      const pin = $('#pin-idea'), cam = $('#cam-idea');
      const plane = $('#fru-plane'), rays = $('#fru-rays'), faces = [1, 2, 3, 4].map((k) => $('#fru-f' + k));
      idea.aim = 0;
      s.onframe = () => {
        place(pin, s.project(CRACK[0], CRACK[1], CRACK[2]));
        const T = lerp3(AIM_FROM, CRACK, easeInOut(clamp(idea.aim, 0, 1)));
        const f = norm(sub(T, PHONE)), r = norm(cross(f, [0, 0, 1])), up = cross(r, f);
        const pc = s.project(PHONE[0], PHONE[1], PHONE[2]);
        const cs = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => { const c = add(T, add(mul(r, a * 1.5), mul(up, b * 1.0))); return s.project(c[0], c[1], c[2]); });
        const pt = (p) => p.u.toFixed(1) + ',' + p.v.toFixed(1);
        plane.setAttribute('points', cs.map(pt).join(' '));
        rays.setAttribute('d', cs.map((p) => 'M' + pt(pc) + 'L' + pt(p)).join(''));
        faces.forEach((el, k) => el.setAttribute('points', pt(pc) + ' ' + pt(cs[k]) + ' ' + pt(cs[(k + 1) % 4])));
        place(cam, pc);
      };
      s.assemble = 0; s.mix = 0; s.start();
      tween(s, 'assemble', 1, 2600, null, () => s.redraw());
      s.redraw();
    },
    step(k, instant) {
      if (k === 1) tween(idea, 'aim', 1, instant ? 0 : 1700, null, () => scenes['sc-idea'].redraw());
    },
    back(st) { if (st < 1) idea.aim = 0; },
    leave() { stopScene('sc-idea'); },
  };

  const vid = $('#vid');
  let vraf = 0;
  hooks.demo = {
    enter() {
      root.style.setProperty('--bg', VIDEO_PAPER);
      if (INSTANT) return;
      try { vid.currentTime = 0; } catch (e) { /* not loaded yet */ }
      const p = vid.play();
      if (p && p.catch) p.catch(() => { $('#vplay').hidden = false; });
      const bar = $('.vbar b');
      const tick = () => { bar.style.width = (vid.duration ? (vid.currentTime / vid.duration) * 100 : 0) + '%'; vraf = requestAnimationFrame(tick); };
      tick();
    },
    leave() { root.style.removeProperty('--bg'); cancelAnimationFrame(vraf); vid.pause(); $('#vplay').hidden = true; },
  };
  vid.addEventListener('playing', () => { $('#vplay').hidden = true; });
  vid.addEventListener('ended', () => {
    if (slides[cur].id !== 'demo') return;
    lockUntil = performance.now() + 1000;      // a click that arrives together with the hand-over must not skip a build
    next(true);
  });

  hooks.proof = {
    enter() {
      $$('#proof .kpi__v[data-count]').forEach((el, i) => {
        const to = +el.dataset.count, node = el.firstChild;
        if (INSTANT) { node.nodeValue = String(to); return; }
        node.nodeValue = '0';
        const o = { v: 0 };
        clearTimeout(el.__t);
        el.__t = setTimeout(() => {
          const t0 = performance.now(), dur = 1300;
          const tick = (now) => {
            const p = clamp((now - t0) / dur, 0, 1); o.v = to * easeOut(p); node.nodeValue = String(Math.round(o.v));
            if (p < 1 && slides[cur].id === 'proof') requestAnimationFrame(tick); else node.nodeValue = String(to);
          };
          requestAnimationFrame(tick);
        }, 700 + i * 150);
      });
    },
  };

  const patrol = { on: 0 };
  hooks.next = {
    enter() {
      const s = getScene('sc-next', { S: 48, cx: 0.743, cy: 0.617, az: -1.15, sway: 0.22, period: 26 });
      const fl = CLOUD.floor, bot = $('#bot'), path = $('#route-path');
      const at = (a) => [fl.x + Math.cos(a) * fl.rx * 1.05, fl.y + Math.sin(a) * fl.ry * 1.05, 0.12];
      patrol.on = 0;
      s.onframe = (sc, now) => {
        let d = '';
        for (let k = 0; k <= 72; k++) { const p = at((k / 72) * 6.2832), q = s.project(p[0], p[1], p[2]); d += (k ? 'L' : 'M') + q.u.toFixed(1) + ' ' + q.v.toFixed(1); }
        path.setAttribute('d', d + 'Z');
        const a = INSTANT ? 2.2 : ((now - s.t0) / 1000) * 0.55, b = at(a);
        place(bot, s.project(b[0], b[1], b[2]));
      };
      s.assemble = 0; s.mix = 0; s.start();
      tween(s, 'assemble', 1, 2200, null, () => s.redraw());
      s.redraw();
      dronePopup.reset();
    },
    step(k, instant) {
      if (k === 1) tween(scenes['sc-next'], 'mix', 1, instant ? 0 : 1400, easeInOut, () => scenes['sc-next'].redraw());
      if (k === 2) dronePopup.open();
    },
    back(st) {
      if (st < 1 && scenes['sc-next']) tween(scenes['sc-next'], 'mix', 0, 600, easeInOut);
      if (st < 2) dronePopup.reset();
    },
    leave() { stopScene('sc-next'); dronePopup.reset(); },
  };

  /* Drones: the simulation clip pops out of the chip, plays once and goes back into it. */
  const dronePopup = {
    vid: $('#vid-drone'), box: $('#vpop'), timer: 0,
    stage(el) {                                         // an element's box in stage pixels (the stage is scaled to the window)
      const k = window.__k || 1, st = $('#stage').getBoundingClientRect(), r = el.getBoundingClientRect();
      return { x: (r.left - st.left) / k, y: (r.top - st.top) / k, w: r.width / k, h: r.height / k };
    },
    layout() {
      const chip = this.stage($('#chip-drone')), box = { x: 700, y: 384, w: 1000, h: 0 };
      box.h = this.box.offsetHeight || 620;
      const cx = chip.x + chip.w / 2, cy = chip.y + chip.h / 2;
      this.box.style.setProperty('--fx', (cx - (box.x + box.w / 2)).toFixed(0) + 'px');
      this.box.style.setProperty('--fy', (cy - (box.y + box.h / 2)).toFixed(0) + 'px');
    },
    open() {
      clearTimeout(this.timer);
      this.layout();
      this.box.classList.remove('closed');
      void this.box.offsetWidth;
      this.box.classList.add('on');
      if (INSTANT) return;
      try { this.vid.currentTime = 0; } catch (e) { /* not loaded yet */ }
      this.timer = setTimeout(() => { const p = this.vid.play(); if (p && p.catch) p.catch(() => {}); }, 450);
    },
    close() {
      this.box.classList.add('closed');
    },
    reset() {
      clearTimeout(this.timer);
      this.vid.pause();
      this.box.classList.remove('on', 'closed');
    },
  };
  dronePopup.vid.addEventListener('ended', () => { if (slides[cur].id === 'next' && step === 2) dronePopup.timer = setTimeout(() => dronePopup.close(), 500); });

  /* ------------------------------------------------------------------ setup */
  function splitWords(el) {
    let i = 0;
    const walk = (node) => {
      const out = document.createDocumentFragment();
      node.childNodes.forEach((ch) => {
        if (ch.nodeType === 3) {
          ch.textContent.split(/(\s+)/).forEach((tok) => {
            if (!tok) return;
            if (/^\s+$/.test(tok)) { out.appendChild(document.createTextNode(' ')); return; }
            const w = document.createElement('span'); w.className = 'w';
            const inner = document.createElement('span'); inner.textContent = tok; inner.style.setProperty('--i', i++);
            w.appendChild(inner); out.appendChild(w);
          });
        } else if (ch.nodeName === 'BR') out.appendChild(ch.cloneNode());
        else { const c = ch.cloneNode(false); c.appendChild(walk(ch)); out.appendChild(c); }
      });
      return out;
    };
    const frag = walk(el); el.textContent = ''; el.appendChild(frag);
  }

  function fit() {
    const k = Math.min(innerWidth / 1920, innerHeight / 1080);
    window.__k = k; root.style.setProperty('--k', k);
  }

  function init() {
    $$('[data-wordmark]').forEach((el) => { el.innerHTML = window.DS_LOGOS.wordmark; });
    const rnd = rng(5);
    $$('.wm g').forEach((g, gi) => $$('circle', g).forEach((c) => {
      c.style.setProperty('--dx', ((rnd() - 0.5) * 120).toFixed(0) + 'px');
      c.style.setProperty('--dy', ((rnd() - 0.5) * 90 - 24).toFixed(0) + 'px');
      c.style.setProperty('--dl', (Math.min(gi, 3) * 0.09 + rnd() * 0.6).toFixed(2) + 's');
    }));
    $$('.reveal').forEach(splitWords);
    $$('.draw').forEach((el) => { if (!el.style.getPropertyValue('--len') && el.getTotalLength) el.style.setProperty('--len', Math.ceil(el.getTotalLength()) + 2); });
    $('#pgn').textContent = String(slides.length).padStart(2, '0');
    fit();

    if (MANIFEST) {
      const out = slides.map((s, i) => ({
        id: s.id, steps: stepsOf(i), chrome: s.dataset.chrome || 'full', notes: NOTES[s.id], video: s.id === 'demo',
        layers: $$('[data-layer]', s).map((el) => ({
          id: el.dataset.layer, step: stepOf(el), anim: el.dataset.pa || el.dataset.a || 'fade',
          delay: el.dataset.pd != null ? +el.dataset.pd : parseFloat(el.style.getPropertyValue('--d')) || 0,
          clip: (() => {                                // a video inside the layer: its file and its box in stage pixels
            const v = $('video', el);
            if (!v) return null;
            let x = 0, y = 0, n = v;
            while (n && n !== s) { x += n.offsetLeft; y += n.offsetTop; n = n.offsetParent; }
            return { src: v.getAttribute('src'), box: [x, y, v.offsetWidth, v.offsetHeight] };
          })(),
          text: el.tagName === 'CANVAS' ? 'Point cloud of the scanned conference hall'
            : (el.textContent || '').replace(/\s+/g, ' ').trim() || el.getAttribute('alt') || ($('img', el) || { alt: '' }).alt || '',
        })),
      }));
      const pre = document.createElement('pre'); pre.id = 'manifest'; pre.textContent = JSON.stringify(out);
      document.body.appendChild(pre);
    }

    let i = 0, st = 0;
    if (EXPORT) { i = clamp((+params.get('export') || 1) - 1, 0, slides.length - 1); st = stepsOf(i); }
    else if (params.has('slide')) { i = clamp(+params.get('slide') - 1, 0, slides.length - 1); st = params.has('step') ? clamp(+params.get('step'), 0, stepsOf(i)) : stepsOf(i); }
    else {
      const m = /^#(\d+)(?:\.(\d+))?$/.exec(location.hash);
      if (m) { i = clamp(+m[1] - 1, 0, slides.length - 1); st = clamp(+(m[2] || 0), 0, stepsOf(i)); }
    }
    show(i, st, true);

    if (EXPORT) {
      const layer = params.get('layer'), s = slides[i];
      if (layer === '__brand') { s.style.visibility = 'hidden'; $('#pager').style.display = 'none'; $('#track').style.display = 'none'; $('#brand').style.opacity = 1; }
      else {
        $('#chrome').style.display = 'none';
        if (layer && layer !== '__all') {
          s.style.visibility = 'hidden';
          const t = s.querySelector('[data-layer="' + layer + '"]');
          if (t) t.style.visibility = 'visible';
        }
      }
    }
    root.dataset.ready = '1';
    if (!INSTANT) { const hint = $('#hint'); setTimeout(() => hint.classList.add('on'), 1200); setTimeout(() => hint.classList.remove('on'), 6000); }
  }

  /* ------------------------------------------------------------------ input */
  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen(); else if (root.requestFullscreen) root.requestFullscreen().catch(() => {});
  }
  addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;
    $('#hint').classList.remove('on');
    if (k === 'ArrowRight' || k === 'PageDown' || k === ' ' || k === 'Enter' || k === 'ArrowDown') { e.preventDefault(); next(); }
    else if (k === 'ArrowLeft' || k === 'PageUp' || k === 'Backspace' || k === 'ArrowUp') { e.preventDefault(); prev(); }
    else if (k === 'Home') show(0, 0);
    else if (k === 'End') show(slides.length - 1, 0);
    else if (/^[1-9]$/.test(k)) { if (+k <= slides.length) show(+k - 1, 0); }
    else if (k === 'f' || k === 'F') toggleFullscreen();
    else if (k === 's' || k === 'S') $('#notes').hidden = !$('#notes').hidden;
    else if (k === 't' || k === 'T') $('#timer').hidden = !$('#timer').hidden;
    else if (k === 'r' || k === 'R') started = null;
    else if (k === 'b' || k === 'B' || k === '.') $('#black').hidden = !$('#black').hidden;
    else if (k === 'p' || k === 'P') { if (slides[cur].id === 'demo') { if (vid.paused) vid.play(); else vid.pause(); } }
  });
  addEventListener('click', (e) => {
    if (e.button !== 0 || e.target.closest('#notes')) return;
    if (!$('#black').hidden) { $('#black').hidden = true; return; }
    if (slides[cur].id === 'demo' && vid.paused && !vid.ended) { vid.play(); return; }
    next();
  });
  addEventListener('hashchange', () => {
    const m = /^#(\d+)(?:\.(\d+))?$/.exec(location.hash);
    if (!m) return;
    const i = clamp(+m[1] - 1, 0, slides.length - 1), st = clamp(+(m[2] || 0), 0, stepsOf(i));
    if (i !== cur || st !== step) show(i, st);
  });
  let tx0 = null;
  addEventListener('touchstart', (e) => { tx0 = e.changedTouches[0].clientX; }, { passive: true });
  addEventListener('touchend', (e) => { if (tx0 === null) return; const dx = e.changedTouches[0].clientX - tx0; tx0 = null; if (Math.abs(dx) > 50) { e.preventDefault(); if (dx < 0) next(); else prev(); } });
  addEventListener('resize', () => { fit(); Object.values(scenes).forEach((s) => s.resize()); });
  let idleT = 0;
  addEventListener('mousemove', () => { document.body.classList.remove('idle'); clearTimeout(idleT); idleT = setTimeout(() => document.body.classList.add('idle'), 2500); });
  setInterval(() => {
    const el = $('#timer'), s = started === null ? 0 : (performance.now() - started) / 1000;
    el.textContent = Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0') + ' / 4:00';
    el.dataset.state = s > TARGET_SECONDS ? 'over' : s > TARGET_SECONDS - 25 ? 'late' : '';
  }, 250);

  const fonts = ['700 88px "Space Grotesk"', '600 36px "Space Grotesk"', '400 30px "IBM Plex Sans"', '600 30px "IBM Plex Sans"', '400 20px "IBM Plex Mono"', '500 20px "IBM Plex Mono"'];
  const ready = document.fonts && document.fonts.load ? Promise.all(fonts.map((f) => document.fonts.load(f))).catch(() => {}) : Promise.resolve();
  Promise.race([ready, new Promise((r) => setTimeout(r, 1500))]).then(init);
})();
