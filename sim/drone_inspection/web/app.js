'use strict';
const $ = id => document.getElementById(id);
let state = {}, socket, frozen = null, connected = false;
const keys = new Set();
const flightKeys = new Set(['w', 's', 'a', 'd', 'r', 'f', 'q', 'e']);
function showError(message) {
  $('error').textContent = message || '';
  $('error').hidden = !message;
}
async function post(path, value) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
}
async function command(value) {
  try { await post('/api/command', value); showError(''); }
  catch (error) { showError(error.message); }
}
function availability() {
  const unavailable = !connected || !state.ready;
  document.querySelectorAll('[data-command]').forEach(button => { button.disabled = unavailable; });
  $('freeze').disabled = unavailable;
  $('manual').disabled = unavailable;
  $('send').disabled = unavailable;
}
function liveFrame() {
  frozen = null;
  $('first').src = '/api/stream/first.mjpg';
  $('first').classList.remove('frozen');
  $('freeze').textContent = 'Freeze to select';
}
function render(value) {
  state = value;
  availability();
  $('action').textContent = value.action || 'Loading scene…';
  $('state').textContent = [value.state, value.paused ? 'paused' : '', value.emergency ? 'emergency stopped' : ''].filter(Boolean).join(' · ');
  if (value.error) showError(value.error);
  if (!value.ready) return;
  const coverage = value.coverage;
  $('coverage').textContent = `${(coverage.fraction * 100).toFixed(1)}%`;
  $('progress').value = coverage.fraction;
  $('position').textContent = value.position.map(x => x.toFixed(2)).join(', ');
  $('telemetry').textContent = `${value.speed.toFixed(2)} m/s · ${value.elapsed.toFixed(1)} s`;
  $('areas').textContent = `${coverage.inspected_area_m2.toFixed(1)} / ${coverage.total_area_m2.toFixed(1)} m² inspected · ${coverage.pending_area_m2.toFixed(1)} m² pending · ${coverage.unreachable_area_m2.toFixed(1)} m² unreachable`;
  $('saved').textContent = value.run_folder ? `Run files: ${value.run_folder}` : '';
  if (value.selection) $('selection').textContent = `Region center: ${value.selection.center.map(x => x.toFixed(2)).join(', ')} m · radius ${value.selection.radius.toFixed(2)} m. Choose Selected region and start inspection.`;
  $('chat').replaceChildren(...(value.chat || []).map(entry => {
    const p = document.createElement('p');
    p.textContent = `${entry.user} · ${entry.status}: ${entry.explanation}`;
    return p;
  }));
}
function connect() {
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/events`);
  socket.onopen = () => { connected = true; $('connection').textContent = '● Connected'; availability(); };
  socket.onmessage = event => render(JSON.parse(event.data));
  socket.onclose = () => {
    connected = false; keys.clear(); $('manual').checked = false;
    $('connection').textContent = 'Disconnected · reconnecting…'; availability();
    setTimeout(connect, 1000);
  };
  socket.onerror = () => socket.close();
}
document.querySelectorAll('[data-command]').forEach(button => {
  button.disabled = true;
  button.addEventListener('click', () => {
    const type = button.dataset.command;
    keys.clear(); $('manual').checked = false;
    if (type === 'reset') { liveFrame(); $('selection').textContent = 'Freeze a frame, then click a surface to preview an inspection region.'; }
    command(type === 'start' ? {type, mode: $('mode').value} : {type});
  });
});
$('freeze').addEventListener('click', () => {
  if (frozen) { liveFrame(); return; }
  if (!state.frame_id) return;
  frozen = {id: state.frame_id, width: state.camera.intrinsics.width, height: state.camera.intrinsics.height};
  $('first').src = `/api/frames/${encodeURIComponent(frozen.id)}.jpg`;
  $('first').classList.add('frozen');
  $('freeze').textContent = 'Return to live';
});
$('first').addEventListener('click', event => {
  if (!frozen) return;
  const rect = event.target.getBoundingClientRect();
  const scale = Math.min(rect.width / frozen.width, rect.height / frozen.height);
  const u = (event.clientX - rect.left - (rect.width - frozen.width * scale) / 2) / scale;
  const v = (event.clientY - rect.top - (rect.height - frozen.height * scale) / 2) / scale;
  if (u < 0 || v < 0 || u >= frozen.width || v >= frozen.height) return;
  if (!$('radius').reportValidity()) return;
  command({type: 'select', frame_id: frozen.id, u, v, radius: Number($('radius').value)});
});
$('chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  try { await post('/api/chat', {message: $('message').value}); $('message').value = ''; showError(''); }
  catch (error) { showError(error.message); }
});
function stopManual() {
  keys.clear();
  if ($('manual').checked && connected && state.ready) command({type: 'hover'});
  $('manual').checked = false;
}
$('manual').addEventListener('change', () => { keys.clear(); if (state.ready) command({type: 'hover'}); });
window.addEventListener('blur', stopManual);
document.addEventListener('visibilitychange', () => { if (document.hidden) stopManual(); });
window.addEventListener('keydown', event => {
  if (!$('manual').checked || /INPUT|SELECT|TEXTAREA|BUTTON/.test(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if (key === ' ') { event.preventDefault(); keys.clear(); command({type: 'hover'}); }
  else if (flightKeys.has(key)) { event.preventDefault(); keys.add(key); }
});
window.addEventListener('keyup', event => { keys.delete(event.key.toLowerCase()); });
setInterval(() => {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'heartbeat'}));
}, 400);
setInterval(() => {
  if (!$('manual').checked || !connected || !state.ready || state.paused || state.emergency) return;
  const axis = (positive, negative) => Number(keys.has(positive)) - Number(keys.has(negative));
  command({type: 'velocity', value: [axis('w', 's') * .4, axis('a', 'd') * .4, axis('r', 'f') * .3, axis('q', 'e') * .5]});
}, 100);
connect();
