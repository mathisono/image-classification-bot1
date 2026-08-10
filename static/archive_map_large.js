(() => {
  const RECORD_BYTES = 16;

  function compile(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  }

  function program(gl, vertex, fragment) {
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vertex));
    gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fragment));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return p;
  }

  async function start() {
    const stage = document.getElementById('archive-map-stage');
    if (!stage) return;
    const card = stage.closest('.card');
    const jsonLink = card && card.querySelector('a[href*="/archive-map/snapshots/"][href$=".json"]');
    if (!jsonLink) return;

    const manifestResponse = await fetch(jsonLink.href, {cache: 'no-store'});
    if (!manifestResponse.ok) return;
    const data = await manifestResponse.json();
    if (data.format !== 'archive-map-v2-binary') return;

    const pointResponse = await fetch(data.points_url, {cache: 'no-store'});
    if (!pointResponse.ok) throw new Error(`Could not load binary points: HTTP ${pointResponse.status}`);
    const raw = await pointResponse.arrayBuffer();
    const stride = Number(data.record_bytes || RECORD_BYTES);
    if (stride !== RECORD_BYTES || raw.byteLength % stride !== 0) throw new Error('Unexpected Archive Map binary record format.');
    const pointCount = raw.byteLength / stride;
    if (pointCount !== Number(data.point_count)) throw new Error(`Point count mismatch: manifest=${data.point_count}, binary=${pointCount}`);

    stage.replaceChildren();
    const canvas = document.createElement('canvas');
    canvas.className = 'archive-map-large-canvas';
    canvas.setAttribute('aria-label', `Archive Map with ${pointCount} points`);
    const labels = document.createElement('div');
    labels.className = 'archive-map-label-layer';
    const hint = document.createElement('div');
    hint.className = 'archive-map-large-hint';
    hint.textContent = 'WebGL large-map mode · drag to pan · wheel to zoom · click a point for details';
    stage.append(canvas, labels, hint);

    const gl = canvas.getContext('webgl', {antialias: false, alpha: false, depth: false, stencil: false});
    if (!gl) throw new Error('WebGL is required for Archive Maps above the large-map threshold.');
    const view = new DataView(raw);

    const vertex = `
      attribute vec2 a_position;
      attribute float a_cluster;
      uniform vec2 u_pan;
      uniform float u_zoom;
      uniform float u_point_size;
      varying float v_cluster;
      void main() {
        vec2 p = (a_position - vec2(0.5) - u_pan) * u_zoom;
        gl_Position = vec4(p.x * 2.0, -p.y * 2.0, 0.0, 1.0);
        gl_PointSize = u_point_size;
        v_cluster = a_cluster;
      }
    `;
    const fragment = `
      precision mediump float;
      varying float v_cluster;
      void main() {
        float t = fract(v_cluster * 0.61803398875);
        vec3 c = 0.58 + 0.42 * cos(6.2831853 * (t + vec3(0.0, 0.33, 0.67)));
        gl_FragColor = vec4(c, 0.86);
      }
    `;
    const pointProgram = program(gl, vertex, fragment);
    const pointBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, pointBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, raw, gl.STATIC_DRAW);

    const highlightVertex = `
      attribute vec2 a_position;
      uniform vec2 u_pan;
      uniform float u_zoom;
      uniform float u_point_size;
      void main() {
        vec2 p = (a_position - vec2(0.5) - u_pan) * u_zoom;
        gl_Position = vec4(p.x * 2.0, -p.y * 2.0, 0.0, 1.0);
        gl_PointSize = u_point_size;
      }
    `;
    const highlightFragment = `precision mediump float; void main(){ gl_FragColor=vec4(1.0,0.9,0.15,1.0); }`;
    const highlightProgram = program(gl, highlightVertex, highlightFragment);
    const highlightBuffer = gl.createBuffer();
    let highlightCount = 0;

    const state = {zoom: 1, panX: 0, panY: 0};
    const labelNodes = (data.labels || []).map(label => {
      const node = document.createElement('div');
      node.className = 'archive-map-large-label';
      node.textContent = label.text;
      node.title = `${label.count} images`;
      labels.appendChild(node);
      return {node, label};
    });

    function uniforms(p, pointSize) {
      gl.uniform2f(gl.getUniformLocation(p, 'u_pan'), state.panX, state.panY);
      gl.uniform1f(gl.getUniformLocation(p, 'u_zoom'), state.zoom);
      gl.uniform1f(gl.getUniformLocation(p, 'u_point_size'), pointSize);
    }

    function draw() {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0.043, 0.063, 0.125, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);

      gl.useProgram(pointProgram);
      gl.bindBuffer(gl.ARRAY_BUFFER, pointBuffer);
      const pos = gl.getAttribLocation(pointProgram, 'a_position');
      const cluster = gl.getAttribLocation(pointProgram, 'a_cluster');
      gl.enableVertexAttribArray(pos);
      gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, stride, 4);
      gl.enableVertexAttribArray(cluster);
      gl.vertexAttribPointer(cluster, 1, gl.FLOAT, false, stride, 12);
      uniforms(pointProgram, Math.min(5.5, 1.6 + Math.log2(Math.max(1, state.zoom)) * 0.65));
      gl.drawArrays(gl.POINTS, 0, pointCount);

      if (highlightCount) {
        gl.useProgram(highlightProgram);
        gl.bindBuffer(gl.ARRAY_BUFFER, highlightBuffer);
        const hp = gl.getAttribLocation(highlightProgram, 'a_position');
        gl.enableVertexAttribArray(hp);
        gl.vertexAttribPointer(hp, 2, gl.FLOAT, false, 0, 0);
        uniforms(highlightProgram, 7.0);
        gl.drawArrays(gl.POINTS, 0, highlightCount);
      }
      updateLabels();
    }

    function updateLabels() {
      const w = stage.clientWidth, h = stage.clientHeight;
      for (const {node, label} of labelNodes) {
        const sx = ((label.x - 0.5 - state.panX) * state.zoom + 0.5) * w;
        const sy = ((label.y - 0.5 - state.panY) * state.zoom + 0.5) * h;
        const visible = sx > -180 && sx < w + 180 && sy > -50 && sy < h + 50;
        node.style.display = visible ? 'block' : 'none';
        if (visible) node.style.transform = `translate(${sx}px,${sy}px) translate(-50%,-50%)`;
      }
    }

    function resize() {
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      const width = Math.max(1, Math.round(stage.clientWidth * ratio));
      const height = Math.max(1, Math.round(stage.clientHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width; canvas.height = height;
      }
      draw();
    }
    new ResizeObserver(resize).observe(stage);

    let drag = null;
    canvas.addEventListener('pointerdown', event => {
      drag = {x: event.clientX, y: event.clientY, panX: state.panX, panY: state.panY, moved: false};
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove', event => {
      if (!drag) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      state.panX = drag.panX - dx / Math.max(1, canvas.clientWidth) / state.zoom;
      state.panY = drag.panY - dy / Math.max(1, canvas.clientHeight) / state.zoom;
      draw();
    });
    canvas.addEventListener('pointerup', async event => {
      if (!drag) return;
      const wasMoved = drag.moved;
      drag = null;
      if (!wasMoved) await selectNearest(event);
    });

    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const sx = (event.clientX - rect.left) / rect.width;
      const sy = (event.clientY - rect.top) / rect.height;
      const oldZoom = state.zoom;
      const mapX = 0.5 + state.panX + (sx - 0.5) / oldZoom;
      const mapY = 0.5 + state.panY + (sy - 0.5) / oldZoom;
      state.zoom = Math.max(1, Math.min(256, oldZoom * (event.deltaY > 0 ? 0.82 : 1.22)));
      state.panX = mapX - 0.5 - (sx - 0.5) / state.zoom;
      state.panY = mapY - 0.5 - (sy - 0.5) / state.zoom;
      draw();
    }, {passive: false});

    async function selectNearest(event) {
      const rect = canvas.getBoundingClientRect();
      const sx = (event.clientX - rect.left) / rect.width;
      const sy = (event.clientY - rect.top) / rect.height;
      const mapX = 0.5 + state.panX + (sx - 0.5) / state.zoom;
      const mapY = 0.5 + state.panY + (sy - 0.5) / state.zoom;
      const px = 12;
      let best = -1, bestD2 = px * px;
      for (let i = 0, off = 0; i < pointCount; i++, off += stride) {
        const x = view.getFloat32(off + 4, true), y = view.getFloat32(off + 8, true);
        const dx = (x - mapX) * state.zoom * rect.width;
        const dy = (y - mapY) * state.zoom * rect.height;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) { bestD2 = d2; best = off; }
      }
      if (best < 0) return;
      const imageId = view.getUint32(best, true);
      const detailResponse = await fetch(`/archive-map/api/points/${imageId}`);
      if (!detailResponse.ok) return;
      const point = await detailResponse.json();
      const editor = document.getElementById('point-editor');
      if (editor) editor.hidden = false;
      const summary = document.getElementById('point-summary');
      if (summary) summary.textContent = `${point.filename} — ${point.short_caption || point.detailed_description || 'No description'}`;
      const open = document.getElementById('point-open');
      if (open) open.href = point.detail_url;
      const idField = document.getElementById('override-image-id');
      if (idField) idField.value = imageId;
      const xField = document.getElementById('override-x');
      const yField = document.getElementById('override-y');
      if (xField) xField.value = view.getFloat32(best + 4, true).toFixed(7);
      if (yField) yField.value = view.getFloat32(best + 8, true).toFixed(7);
    }

    const search = document.getElementById('map-search');
    const count = document.getElementById('map-count');
    if (count) count.textContent = `${pointCount.toLocaleString()} points · ${data.cluster_count} labels · binary WebGL`;
    let searchTimer = null;
    if (search) search.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(async () => {
        const query = search.value.trim();
        if (!query) {
          highlightCount = 0;
          if (count) count.textContent = `${pointCount.toLocaleString()} points · ${data.cluster_count} labels`;
          draw();
          return;
        }
        const response = await fetch(`${data.search_url}?q=${encodeURIComponent(query)}&limit=10000`);
        if (!response.ok) return;
        const result = await response.json();
        const wanted = new Set(result.ids);
        const coords = new Float32Array(wanted.size * 2);
        let n = 0;
        for (let i = 0, off = 0; i < pointCount; i++, off += stride) {
          if (!wanted.has(view.getUint32(off, true))) continue;
          coords[n * 2] = view.getFloat32(off + 4, true);
          coords[n * 2 + 1] = view.getFloat32(off + 8, true);
          n++;
        }
        highlightCount = n;
        gl.bindBuffer(gl.ARRAY_BUFFER, highlightBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, coords.subarray(0, n * 2), gl.DYNAMIC_DRAW);
        if (count) count.textContent = `${n.toLocaleString()} matches${result.truncated ? ' shown (result limit reached)' : ''} · ${pointCount.toLocaleString()} total`;
        draw();
      }, 250);
    });

    const reset = document.getElementById('reset-view');
    if (reset) reset.addEventListener('click', () => {
      state.zoom = 1; state.panX = 0; state.panY = 0; draw();
    });
    resize();
  }

  window.addEventListener('load', () => setTimeout(() => start().catch(error => {
    const stage = document.getElementById('archive-map-stage');
    if (stage) stage.innerHTML = `<div class="error">Large-map renderer failed: ${String(error)}</div>`;
  }), 120));
})();
