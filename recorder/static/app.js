// Call Recorder UI — vanilla JS, talks to the local Python server.

const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'html') node.innerHTML = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CallRec': '1' },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || res.statusText);
    err.hint = data.hint;
    throw err;
  }
  return data;
}

// ---------------------------------------------------------------------------------------------
// icons (20×20, stroked) and the moving bars

const ICONS = {
  home: '<path d="M3.5 9.5 10 4l6.5 5.5V16a1 1 0 0 1-1 1H12v-4.5H8V17H4.5a1 1 0 0 1-1-1z"/>',
  calendar: '<rect x="3" y="4.5" width="14" height="12.5" rx="2.5"/><path d="M3 8.5h14M7 3v3M13 3v3"/>',
  notes: '<path d="M6.5 3h5.5l4 4v9.5a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 5 16.5v-12A1.5 1.5 0 0 1 6.5 3z"/><path d="M12 3v4h4M8 11h6M8 14h4"/>',
  search: '<circle cx="9" cy="9" r="5.25"/><path d="m13 13 3.5 3.5"/>',
  settings: '<path d="M4 6.5h7M15 6.5h1M4 13.5h1M9 13.5h7"/><circle cx="13" cy="6.5" r="2"/><circle cx="7" cy="13.5" r="2"/>',
  compose: '<path d="M10 4H5.5A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16h9a1.5 1.5 0 0 0 1.5-1.5V10"/><path d="M14.2 3.3a1.5 1.5 0 0 1 2.1 2.1L10 11.7l-2.8.7.7-2.8z"/>',
  people: '<circle cx="8" cy="7.5" r="2.75"/><path d="M3 16.5c.5-2.6 2.5-4 5-4s4.5 1.4 5 4M13 5a2.6 2.6 0 0 1 0 5M14.8 12.8c1.1.5 1.9 1.7 2.2 3.7"/>',
  clock: '<circle cx="10" cy="10" r="6.75"/><path d="M10 6.5V10l2.5 1.75"/>',
  video: '<rect x="2.75" y="5.5" width="10.5" height="9" rx="2"/><path d="m13.25 9 4-2.25v6.5l-4-2.25"/>',
  more: '<circle cx="5" cy="10" r=".9" fill="currentColor"/><circle cx="10" cy="10" r=".9" fill="currentColor"/><circle cx="15" cy="10" r=".9" fill="currentColor"/>',
  back: '<path d="M12 5 7 10l5 5"/>',
  refresh: '<path d="M15.5 10a5.5 5.5 0 1 1-1.8-4.1M15.5 4v3.5H12"/>',
  close: '<path d="m6 6 8 8M14 6l-8 8"/>',
  stop: '<rect x="6" y="6" width="8" height="8" rx="1.5" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="5.5" width="2.75" height="9" rx="1" fill="currentColor" stroke="none"/><rect x="11.25" y="5.5" width="2.75" height="9" rx="1" fill="currentColor" stroke="none"/>',
  record: '<circle cx="10" cy="10" r="4.5" fill="currentColor" stroke="none"/>',
  share: '<path d="M10 3.5v9M6.5 7 10 3.5 13.5 7M5 11v4.5A1.5 1.5 0 0 0 6.5 17h7a1.5 1.5 0 0 0 1.5-1.5V11"/>',
  download: '<path d="M10 3.5v9M6.5 9 10 12.5 13.5 9M5 11v4.5A1.5 1.5 0 0 0 6.5 17h7a1.5 1.5 0 0 0 1.5-1.5V11"/>',
  folder: '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h3.2l1.6 1.75h6.2A1.5 1.5 0 0 1 17 8.25v6.25A1.5 1.5 0 0 1 15.5 16h-11A1.5 1.5 0 0 1 3 14.5z"/>',
  copy: '<rect x="7" y="7" width="9" height="9" rx="1.75"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-6A1.5 1.5 0 0 0 4 5.5v6A1.5 1.5 0 0 0 5.5 13H7"/>',
  redo: '<path d="M15.5 10a5.5 5.5 0 1 1-1.8-4.1M15.5 4v3.5H12"/>',
  check: '<path d="m5 10.5 3.25 3L15 6.5"/>',
  chevron: '<path d="m6.5 8.25 3.5 3.5 3.5-3.5"/>',
  up: '<path d="m6.5 11.75 3.5-3.5 3.5 3.5"/>',
  play: '<path d="M7.25 5.5v9l7.25-4.5z"/>',
  spark: '<path d="M10 3.25 11.6 8.4l5.15 1.6-5.15 1.6L10 16.75 8.4 11.6 3.25 10 8.4 8.4z"/>',
  todo: '<rect x="3.5" y="3.5" width="13" height="13" rx="3.25"/><path d="m7 10.25 2.1 2.1L13.25 8"/>',
  trash: '<path d="M4 6h12M8.25 6V4.5h3.5V6M5.75 6l.65 9.6A1.5 1.5 0 0 0 7.9 17h4.2a1.5 1.5 0 0 0 1.5-1.4L14.25 6M8.5 9v5M11.5 9v5"/>',
  blank: '',
};

function icon(name) {
  const span = document.createElement('span');
  span.className = 'ico';
  span.innerHTML = `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
  return span;
}

// kind: 'live' (green, follows the audio levels) or 'static'
const barsEl = (kind) => h('span', { class: `bars ${kind}${kind === 'live' ? ' idle' : ''}`, 'aria-hidden': 'true' }, h('i'), h('i'), h('i'), h('i'));

function setLevels(d) {
  const level = (x) => (x ? Math.max(0, Math.min(1, (20 * Math.log10(Math.max(x.rms, 1e-6)) + 55) / 45)) : 0);
  const mic = level(d.mic), sys = level(d.system);
  const heights = [mic * 0.9, Math.max(mic, sys), sys * 0.95, ((mic + sys) / 2) * 0.85]
    .map((v) => Math.min(100, 22 + v * 78 * (0.85 + Math.random() * 0.3)));
  document.querySelectorAll('.bars.live').forEach((b) => {
    b.classList.remove('idle');
    b.querySelectorAll('i').forEach((bar, k) => { bar.style.height = `${heights[k]}%`; });
  });
}

// ---------------------------------------------------------------------------------------------
// formatting

const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
function fmtDur(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const hrs = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return hrs ? `${hrs}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}
function fmtLength(sec) {
  const min = Math.round((sec || 0) / 60);
  if (min < 1) return `${Math.round(sec || 0)} sec`;
  return min >= 60 ? `${Math.floor(min / 60)} h${min % 60 ? ` ${min % 60} min` : ''}` : `${min} min`;
}
function dayKey(iso) { const d = new Date(iso); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }
function dayWord(iso) {
  const key = dayKey(iso);
  if (key === dayKey(new Date())) return 'Today';
  if (key === dayKey(new Date(Date.now() + 864e5))) return 'Tomorrow';
  if (key === dayKey(new Date(Date.now() - 864e5))) return 'Yesterday';
  const d = new Date(iso);
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric',
    year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
}
function conferenceName(url) {
  if (!url) return '';
  const known = [['zoom.us', 'Zoom'], ['meet.google', 'Google Meet'], ['teams.', 'Teams'], ['webex', 'Webex'], ['facetime', 'FaceTime'], ['slack.com', 'Slack huddle']];
  return (known.find(([k]) => url.includes(k)) || [null, 'Video call'])[1];
}
// "Luwam, Emily +2"
function shortNames(people, max = 2) {
  const list = people.map((p) => (typeof p === 'string' ? p : p.name || p.email)).filter(Boolean);
  if (list.length === 1) return list[0];
  const first = list.map((n) => (n.includes('@') ? n : n.split(' ')[0]));
  return list.length > max ? `${first.slice(0, max).join(', ')} +${list.length - max}` : first.join(', ');
}
// Seconds recorded so far: pauses don't count (`clock` is when the call's first second would have been).
const elapsed = (rec) => (rec.paused ? rec.elapsed : (Date.now() - new Date(rec.clock || rec.started_at).getTime()) / 1000);
const STATUS_LABEL = {
  recording: 'Recording', recorded: 'Not transcribed', processing: 'Processing', transcribing: 'Transcribing',
  summarizing: 'Writing notes', done: '', error: 'Error',
};

// ---------------------------------------------------------------------------------------------
// state

let state = { recording: null, meetings: [], calendar: { events: [] }, jobs: {}, settings: {}, prompt: null, waiting: null };
let view = { kind: 'home' };   // home | upcoming | todos | calls | live | meeting
let detail = null;              // currently shown meeting detail
let banner = null;              // { kind, event }
let search = '';
let transcriptOpen = false;
let audio = null;               // { id, el } — kept across re-renders so playback isn't interrupted

function toast(message, level = 'info', hint = '') {
  const node = h('div', { class: `toast ${level}` }, message, hint ? h('div', { class: 'hint' }, hint) : null);
  $('#toasts').append(node);
  setTimeout(() => node.remove(), level === 'error' ? 9000 : 4500);
}

async function loadState() {
  state = await api('/api/state');
  if (state.recording && ['home', 'upcoming'].includes(view.kind) && !view.pinned) view = { kind: 'live' };
  if (state.prompt && !state.recording) banner = { kind: 'starting', event: state.prompt };
  waitingBanner();
  renderAll();
}

function renderAll() {
  renderSidebar();
  renderBanner();
  renderContent();
  renderDock();
}

function go(next) {
  view = next;
  if (next.kind !== 'meeting') detail = null;
  closeMenu();
  syncHash();
  renderSidebar();
  renderContent();
  renderDock();
  $('.main').scrollTop = 0;
}

// The address reflects the page (#upcoming, #calls, #note/<id>), so Back works and pages can be linked.
function pageHash() {
  if (view.kind === 'meeting') return `#note/${encodeURIComponent(view.id)}`;
  if (view.kind === 'event') return `#upcoming/${encodeURIComponent(view.id)}`;
  if (view.kind === 'calls' && search) return `#search/${encodeURIComponent(search)}`;
  return ['upcoming', 'todos', 'calls'].includes(view.kind) ? `#${view.kind}` : '';
}
function syncHash() {
  const hash = pageHash();
  if (location.hash !== hash) history.pushState(null, '', hash || location.pathname);
}
function applyHash() {
  const hash = location.hash;
  if (hash.startsWith('#note/')) return openMeeting(decodeURIComponent(hash.slice(6)));
  if (hash.startsWith('#upcoming/')) return openEvent(decodeURIComponent(hash.slice(10)));
  else if (hash.startsWith('#search/')) { search = decodeURIComponent(hash.slice(8)); go({ kind: 'calls' }); }
  else if (hash === '#calls') { search = ''; go({ kind: 'calls', pinned: true }); }
  else if (hash === '#upcoming') go({ kind: 'upcoming', pinned: true });
  else if (hash === '#todos') go({ kind: 'todos', pinned: true });
  else if (view.kind !== 'live') go({ kind: state.recording && !view.pinned ? 'live' : 'home', pinned: true });
}
window.addEventListener('popstate', applyHash);

// ---------------------------------------------------------------------------------------------
// sidebar

$('#new-btn').append(icon('compose'));
$('#search-icon').replaceWith(icon('search'));
$('#nav-home').append(icon('home'), h('span', { class: 'label' }, 'Home'));
$('#nav-upcoming').append(icon('calendar'), h('span', { class: 'label' }, 'Upcoming'), h('span', { class: 'count', id: 'count-upcoming' }));
$('#nav-todos').append(icon('todo'), h('span', { class: 'label' }, 'Live To-Dos'), h('span', { class: 'count', id: 'count-todos' }));
$('#nav-calls').append(icon('notes'), h('span', { class: 'label' }, 'All notes'), h('span', { class: 'count', id: 'count-calls' }));
$('#settings-btn').append(icon('settings'), h('span', { class: 'label' }, 'Settings'));
$('#tpanel-close').append(icon('close'));

function upcomingCalls() {
  const now = Date.now();
  return (state.calendar.events || []).filter((e) => e.is_call && !e.declined && new Date(e.end).getTime() > now);
}

function renderSidebar() {
  const toRecord = upcomingCalls().filter((e) => e.plan !== 'skip').length;
  $('#count-upcoming').textContent = toRecord || '';
  $('#count-calls').textContent = state.meetings.length || '';
  $('#count-todos').textContent = openTodos() || '';
  // A call open from the recent list is highlighted there instead of in the nav.
  const recent = state.meetings.slice(0, RECENT);
  const openId = view.kind === 'meeting' ? view.id : null;
  const active = view.kind === 'meeting' ? (recent.some((m) => m.id === openId) ? null : view.from)
    : view.kind === 'event' ? view.from : view.kind;
  for (const [id, kind] of [['#nav-home', 'home'], ['#nav-upcoming', 'upcoming'], ['#nav-todos', 'todos'], ['#nav-calls', 'calls']]) {
    $(id).classList.toggle('active', active === kind);
  }
  renderRecent(recent, openId);

  const rec = state.recording, live = $('#side-live');
  live.hidden = !rec;
  if (rec) {
    live.classList.toggle('paused', !!rec.paused);
    live.replaceChildren(barsEl(rec.paused ? 'static' : 'live'),
      h('span', { class: 'what' }, h('b', {}, rec.paused ? 'Paused' : 'Recording'), h('span', {}, rec.title)),
      h('span', { class: 't', id: 'side-timer' }, fmtDur(elapsed(rec))));
  }

  const status = $('#side-status');
  const busy = Object.keys(state.jobs || {}).length;
  const next = state.calendar.next;
  status.replaceChildren();
  if (busy) status.append(`Writing notes for ${busy} call${busy > 1 ? 's' : ''}…`);
  else if (next) status.append('Next call ', h('b', {}, `${dayWord(next.start) === 'Today' ? '' : `${dayWord(next.start)} `}${fmtTime(next.start)}`), ` · ${next.title}`);
}

// The newest calls under the nav, by day, so any of them is one click away.
const RECENT = 50;
let recentShown = null;
function renderRecent(recent, openId) {
  const items = [];
  let lastDay = null;
  for (const m of recent) {
    if (m.started_at && dayKey(m.started_at) !== lastDay) {
      lastDay = dayKey(m.started_at);
      items.push(h('div', { class: 'recent-day' }, dayWord(m.started_at)));
    }
    const live = state.recording && state.recording.id === m.id;
    const busy = live || state.jobs[m.id] || ['processing', 'transcribing', 'summarizing'].includes(m.status);
    const open = () => (live ? go({ kind: 'live' }) : openMeeting(m.id));
    items.push(h('div', {
      class: `recent-item${m.id === openId ? ' active' : ''}`, role: 'button', tabindex: '0', title: m.title || m.id,
      'data-id': m.id, onclick: open, onkeydown: (e) => { if (e.key === 'Enter') open(); },
      oncontextmenu: (e) => { e.preventDefault(); callMenu(m, e); },
    },
      h('span', { class: 'recent-title' }, m.title || m.id),
      busy ? h('span', { class: 'spinner' }) : m.started_at ? h('span', { class: 'recent-time' }, fmtTime(m.started_at)) : null,
      busy ? null : moreButton(m)));
  }
  if (state.meetings.length > recent.length) {
    items.push(h('button', { class: 'recent-more', onclick: () => $('#nav-calls').click() }, `All ${state.meetings.length} notes`));
  }
  const box = $('#side-recent');
  box.hidden = !recent.length;
  box.replaceChildren(...items);
  // Keep the open call in sight when it was opened from somewhere else (search, Home…).
  if (openId && openId !== recentShown) box.querySelector('.recent-item.active')?.scrollIntoView({ block: 'nearest' });
  recentShown = openId;
}

$('#nav-home').addEventListener('click', () => go({ kind: 'home', pinned: true }));
$('#nav-upcoming').addEventListener('click', () => go({ kind: 'upcoming', pinned: true }));
$('#nav-todos').addEventListener('click', () => { go({ kind: 'todos', pinned: true }); loadTodos(); });
$('#nav-calls').addEventListener('click', () => { search = ''; $('#side-search').value = ''; go({ kind: 'calls' }); });
$('#side-live').addEventListener('click', () => go({ kind: 'live' }));
$('#new-btn').addEventListener('click', () => (state.recording ? go({ kind: 'live' }) : startRecording()));

// The sidebar's width: drag its edge (or focus the edge and use ← →); double-click puts it back. Kept in this
// window's own storage, so the app window and a browser each remember theirs.
const SIDE_DEFAULT = 248, SIDE_MIN = 200;
let sideWanted = SIDE_DEFAULT;  // what you chose; a narrow window can hold it back without forgetting it
try { sideWanted = Number(localStorage.getItem('side-width')) || SIDE_DEFAULT; } catch {}

function applySideWidth() {
  const width = Math.round(Math.max(SIDE_MIN, Math.min(sideWanted, 560, innerWidth - 420)));
  document.documentElement.style.setProperty('--side-w', `${width}px`);
  $('#side-resize').setAttribute('aria-valuenow', String(width));
  return width;
}

function chooseSideWidth(px, save) {
  sideWanted = px;
  sideWanted = applySideWidth();
  if (save) try { localStorage.setItem('side-width', String(sideWanted)); } catch {}
}

$('#side-resize').addEventListener('pointerdown', (e) => {
  if (e.button !== 0) return;
  e.preventDefault();
  const handle = e.currentTarget;
  handle.setPointerCapture(e.pointerId);
  document.body.classList.add('resizing');
  const move = (ev) => chooseSideWidth(ev.clientX, false);
  const done = () => {
    handle.removeEventListener('pointermove', move);
    handle.removeEventListener('pointerup', done);
    handle.removeEventListener('pointercancel', done);
    document.body.classList.remove('resizing');
    chooseSideWidth(sideWanted, true);
  };
  handle.addEventListener('pointermove', move);
  handle.addEventListener('pointerup', done);
  handle.addEventListener('pointercancel', done);
});
$('#side-resize').addEventListener('dblclick', () => {
  chooseSideWidth(SIDE_DEFAULT, false);
  try { localStorage.removeItem('side-width'); } catch {}
});
$('#side-resize').addEventListener('keydown', (e) => {
  const step = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
  if (step) { e.preventDefault(); chooseSideWidth(applySideWidth() + step, true); }
});
addEventListener('resize', applySideWidth);
applySideWidth();

// The sidebar box is a shortcut to the search page, like ⌘K.
$('#side-search').addEventListener('focus', (e) => { e.target.blur(); openSearch(); });
$('.side-search').addEventListener('mousedown', (e) => { e.preventDefault(); openSearch(); });

// ---------------------------------------------------------------------------------------------
// banner: a call is starting / waiting for you to join / is past its end

function renderBanner() {
  const node = $('#banner');
  node.replaceChildren();
  if (!banner) { node.hidden = true; return; }
  node.hidden = false;
  const ev = banner.event;
  if (banner.kind === 'starting') {
    const conf = conferenceName(ev.conference_url);
    node.append(barsEl('static'),
      h('span', { class: 'msg' }, h('b', {}, ev.title), ` is starting${conf ? ` on ${conf}` : ''}`),
      ev.conference_url ? h('a', { href: ev.conference_url, target: '_blank', class: 'link' }, 'Join') : null,
      h('button', { class: 'primary', onclick: () => recordWhenJoined(ev) }, 'Record'),
      h('button', { class: 'ghost', onclick: () => { banner = null; api('/api/prompt/dismiss', {}); renderBanner(); } }, 'Not now'));
  } else if (banner.kind === 'waiting') {
    node.append(barsEl('static'),
      h('span', { class: 'msg' }, 'Recording ', h('b', {}, ev.title), ' when you join the call'),
      ev.conference_url ? h('a', { href: ev.conference_url, target: '_blank', class: 'link' }, 'Join') : null,
      h('button', { class: 'secondary', title: 'Start recording right away', onclick: () => startRecording({ event_id: ev.event_id }) }, 'Start now'),
      h('button', { class: 'ghost', onclick: cancelWait }, 'Don’t record'));
  } else if (banner.kind === 'over') {
    node.append(h('span', { class: 'msg' }, h('b', {}, ev.title), ` was scheduled to end at ${fmtTime(ev.end)}`),
      h('button', { class: 'dark', onclick: stopRecording }, 'Stop recording'),
      h('button', { class: 'ghost', onclick: () => { banner = null; renderBanner(); } }, 'Keep going'));
  }
}

// The banner shows the call waiting for you to join, while there is one (and nothing's recording).
function waitingBanner() {
  if (state.waiting && !state.recording) banner = { kind: 'waiting', event: state.waiting };
  else if (banner && banner.kind === 'waiting') banner = null;
}

// ---------------------------------------------------------------------------------------------
// recording

// Record a call that's starting from when you're in it: right away if you already are, else once you join.
async function recordWhenJoined(ev) {
  try {
    banner = null;
    await flushPrep();
    const res = await api('/api/record/start', { event_id: ev.id, when_joined: true });
    if (res.waiting) {
      state.waiting = res.waiting;
      waitingBanner();
      renderBanner();
      if (['home', 'upcoming', 'event'].includes(view.kind)) renderContent();
    } else {
      view = { kind: 'live' };
      await loadState();
    }
  } catch (e) {
    toast(`Couldn't start recording: ${e.message}`, 'error', e.hint);
    renderBanner();
  }
}

async function cancelWait() {
  try {
    await api('/api/record/wait/cancel', {});
    state.waiting = null;
    waitingBanner();
    renderBanner();
    if (['home', 'upcoming', 'event'].includes(view.kind)) renderContent();
  } catch (e) {
    toast(e.message, 'error');
  }
}

let starting = false;
async function startRecording(opts = {}) {
  if (starting) return;
  starting = true;
  $('#new-btn').disabled = true;
  const slow = setTimeout(() => toast('Starting… answer any macOS prompts that appear.'), 1500);
  try {
    banner = null;
    await flushPrep();  // notes just typed for this call go into it
    const rec = await api('/api/record/start', { title: opts.title || '', event_id: opts.event_id || '' });
    state.recording = rec;
    view = { kind: 'live' };
    await loadState();
  } catch (e) {
    toast(`Couldn't start recording: ${e.message}`, 'error', e.hint);
  } finally {
    clearTimeout(slow);
    starting = false;
    $('#new-btn').disabled = false;
  }
}

async function pauseRecording() {
  try {
    state.recording = await api('/api/record/pause', {});
    renderSidebar();
    renderDock();
  } catch (e) {
    toast(`Couldn’t pause: ${e.message}`, 'error', e.hint);
  }
}

async function resumeRecording() {
  try {
    state.recording = await api('/api/record/resume', {});
    renderSidebar();
    renderDock();
  } catch (e) {
    toast(`Couldn’t resume recording: ${e.message}`, 'error', e.hint);
  }
}

// Record more onto a finished call: what's said now is added to its end, and its notes are written again.
// Works while its notes are still being written (the server calls that off) and while another call is
// recording (a second click, or the ⋯ menu item that says so, stops that one first).
let resuming = null;     // id of the call the server is getting ready to record onto
let resumeArmed = null;  // { id, timer }: clicked once while another call is recording

function otherRecording(id) {
  return state.recording && state.recording.id !== id ? state.recording : null;
}

function recordOntoButton(meta) {
  if (state.recording && state.recording.id === meta.id) return null;
  if (resuming === meta.id) return h('button', { class: 'secondary rec-onto', disabled: true }, icon('record'), 'Resuming…');
  const other = otherRecording(meta.id);
  const armed = other && resumeArmed && resumeArmed.id === meta.id;
  const busy = state.jobs[meta.id] || ['processing', 'transcribing', 'summarizing'].includes(meta.status);
  const why = other ? `Stops recording “${other.title}” and keeps recording this call`
    : busy ? 'Keep recording this call: its notes are written again, with the new part, when you stop'
    : 'Keep recording this call: what’s said now is added to the end of it';
  return h('button', { class: `${armed ? 'primary' : 'secondary'} rec-onto`, title: why, onclick: () => recordOnto(meta.id) },
    icon('record'), armed ? `Stop “${other.title}” and resume` : 'Resume recording');
}

async function recordOnto(id, { confirmed = false } = {}) {
  const other = otherRecording(id);
  if (other && !confirmed && !(resumeArmed && resumeArmed.id === id)) {
    clearTimeout(resumeArmed?.timer);  // first click: the button asks before stopping that recording
    resumeArmed = { id, timer: setTimeout(() => { resumeArmed = null; renderDock(); }, 6000) };
    renderDock();
    return;
  }
  clearTimeout(resumeArmed?.timer);
  resumeArmed = null;
  resuming = id;
  renderDock();
  try {
    const rec = await api(`/api/meetings/${encodeURIComponent(id)}/record`, other ? { stop_current: true } : {});
    state.recording = rec;
    view = { kind: 'live' };
    syncHash();  // off the call's #note address, so a reload comes back to the live page
    resuming = null;
    await loadState();
    toast(other ? `Stopped recording “${other.title}”. Recording this call again: what’s said now is added to it.`
      : 'Recording again. What’s said now is added to this call, and its notes are written again when you stop.');
  } catch (e) {
    toast(`Couldn’t resume recording: ${e.message}`, 'error', e.hint);
  } finally {
    if (resuming === id) { resuming = null; renderDock(); }
  }
}

async function stopRecording() {
  try {
    banner = null;
    const { id } = await api('/api/record/stop', {});
    state.recording = null;
    await loadState();
    openMeeting(id);
  } catch (e) {
    toast(e.message, 'error');
  }
}

setInterval(() => {
  if (!state.recording) return;
  const t = fmtDur(elapsed(state.recording));
  for (const id of ['#side-timer', '#dock-timer']) { const n = $(id); if (n) n.textContent = t; }
}, 500);

// ---------------------------------------------------------------------------------------------
// content views

function renderContent() {
  const root = $('#content');
  if (view.kind !== 'event') delete root.dataset.event;
  if (view.kind === 'live' && state.recording) {
    if (root.dataset.live !== state.recording.id) renderLive(root);
    return;
  }
  delete root.dataset.live;
  root.classList.toggle('wide', view.kind === 'todos');  // a column per person
  if (view.kind === 'live') view = { kind: 'home' };
  if (view.kind === 'meeting') return detail && detail.meta.id === view.id ? renderMeeting(root) : null;
  if (view.kind === 'calls') return renderCalls(root);
  if (view.kind === 'upcoming') return renderUpcoming(root);
  if (view.kind === 'event') return renderEvent(root);
  if (view.kind === 'todos') return renderTodos(root);
  return renderHome(root);
}

const notice = (...children) => h('div', { class: 'notice' }, ...children);
const settingsLink = (text) => h('button', { class: 'link', onclick: openSettings }, text);

function calendarProblem(cal) {
  if (cal.source === 'off') return notice('The calendar is turned off. ', settingsLink('Turn it on in Settings'));
  if (cal.error) return notice(`Couldn't read your calendar: ${cal.error} `, settingsLink('Settings'));
  return null;
}

function dateTile(iso) {
  const d = new Date(iso), word = dayWord(iso);
  return h('div', { class: 'date-tile' },
    h('div', { class: 'wd' }, ['Today', 'Tomorrow', 'Yesterday'].includes(word) ? word : d.toLocaleDateString([], { weekday: 'short' })),
    h('div', { class: 'dn' }, d.getDate()),
    h('div', { class: 'mo' }, d.toLocaleDateString([], { month: 'short' })));
}

function eventRow(ev, now) {
  const start = new Date(ev.start).getTime(), end = new Date(ev.end).getTime();
  const isNow = start <= now && now < end, isPast = end <= now;
  const conf = conferenceName(ev.conference_url);
  const joinable = ev.conference_url && !isPast && start - now < 15 * 60e3;
  const sub = h('div', { class: 'sub' }, `${fmtTime(ev.start)} – ${fmtTime(ev.end)}`,
    ev.others && ev.others.length ? ` · ${shortNames(ev.others)}` : '',
    conf ? ' · ' : '', joinable ? h('a', { href: ev.conference_url, target: '_blank' }, `Join ${conf}`) : conf);
  const hasNotes = !!(ev.my_notes || '').trim();

  const cls = `event plan-${ev.plan || 'remind'}${isNow ? ' now' : ''}${isPast ? ' past' : ''}${ev.declined ? ' declined' : ''}`;
  return h('div', {
    class: cls, role: 'button', tabindex: '0',
    title: ev.recording_now || ev.meeting_id ? '' : hasNotes ? 'Open your notes for this call' : 'Write notes for this call',
    onclick: (e) => { if (!e.target.closest('button, a')) openEventRow(ev); },
    onkeydown: (e) => { if (e.key === 'Enter' && e.target === e.currentTarget) openEventRow(ev); },
  },
    h('span', { class: 'rail' }),
    h('div', { class: 'body' }, h('div', { class: 'title' }, ev.title,
      hasNotes ? h('span', { class: 'has-notes', title: 'You’ve written notes for this call' }, icon('notes')) : null), sub),
    h('div', { class: 'side' }, eventSide(ev, now)));
}

// What a call offers on its right: its recording, its notes, Record now, or what happens when it starts.
function eventSide(ev, now) {
  const start = new Date(ev.start).getTime(), end = new Date(ev.end).getTime();
  if (ev.declined) return h('span', { class: 'pill' }, 'Declined');
  if (ev.recording_now) return h('button', { class: 'pill ok', onclick: () => go({ kind: 'live' }) }, barsEl('live'), 'Recording');
  if (ev.meeting_id) return h('button', { class: 'secondary', onclick: () => openMeeting(ev.meeting_id) }, 'Open notes');
  if (state.waiting && state.waiting.event_id === ev.id && !state.recording) {
    return h('span', { class: 'pill ok', title: 'Recording starts as soon as you’re in the call' }, 'Records when you join');
  }
  if (end <= now) return h('span', { class: 'pill' }, 'Not recorded');
  if (start <= now && !state.recording) return h('button', { class: 'primary', onclick: () => startRecording({ event_id: ev.id }) }, 'Record now');
  return planControl(ev);
}

// A call opens to its notes: the recording's once there is one, before that the notes you write for it.
function openEventRow(ev) {
  if (ev.recording_now) return go({ kind: 'live' });
  if (ev.meeting_id) return openMeeting(ev.meeting_id);
  openEvent(ev.id);
}

const PLAN_LABEL = { record: 'Will record', remind: 'Remind me', skip: 'Won’t record' };

// What happens when the call starts: a quiet pill showing the choice, with a menu to change it.
function planControl(ev) {
  const set = (value) => () => {
    const fallback = state.calendar.auto_record ? 'record' : 'remind';
    api('/api/calendar/plan', { id: ev.id, plan: value === fallback ? '' : value })
      .then((cal) => { state.calendar = cal; renderSidebar(); renderContent(); })
      .catch((e) => toast(e.message, 'error'));
  };
  const plan = ev.plan || 'remind';
  const btn = h('button', { class: `plan-btn plan-${plan}`, title: 'What happens when this call starts' },
    plan === 'record' ? h('span', { class: 'dot' }) : null, PLAN_LABEL[plan], icon('chevron'));
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    openMenu(btn, [['record', 'Record it automatically'], ['remind', 'Remind me when it starts'], ['skip', 'Don’t record or remind']]
      .map(([value, label]) => [value === plan ? 'check' : 'blank', label, set(value)]));
  });
  return btn;
}

// One card: a date tile per day, that day's calls beside it.
function eventCard(events, now, perDay = Infinity) {
  const days = [];
  for (const ev of events) {
    let day = days[days.length - 1];
    if (!day || day.key !== dayKey(ev.start)) days.push(day = { key: dayKey(ev.start), start: ev.start, events: [] });
    if (day.events.length < perDay) day.events.push(ev);
  }
  return h('div', { class: 'upnext' }, days.map((d) => h('div', { class: 'upnext-day' },
    dateTile(d.start), h('div', { class: 'upnext-events' }, d.events.map((ev) => eventRow(ev, now))))));
}

function noteRow(m, withSnippet = false) {
  const job = state.jobs[m.id];
  const status = job ? job.step : m.status;
  const label = STATUS_LABEL[status] ?? '';
  const pillCls = status === 'error' || status === 'recording' ? 'err' : 'busy';
  const who = [m.participants && m.participants.length ? shortNames(m.participants, 3) : '', m.duration ? fmtLength(m.duration) : '']
    .filter(Boolean).join(' · ');
  const snippet = withSnippet ? (m.match || m.summary || '').replace(/\*\*|__|`/g, '') : '';
  return h('div', { class: `note-row${snippet ? ' tall' : ''}`, onclick: () => openMeeting(m.id),
    oncontextmenu: (e) => { e.preventDefault(); callMenu(m, e); } },
    h('span', { class: 'note-icon' }, icon('notes')),
    h('div', { class: 'body' },
      h('div', { class: 'title' }, m.title || m.id, m.source === 'granola' ? h('span', { class: 'source-tag' }, 'Granola') : null),
      who ? h('div', { class: 'who' }, who) : null,
      snippet ? h('div', { class: 'snippet' }, snippet) : null),
    h('div', { class: 'meta' },
      label ? h('span', { class: `pill ${pillCls}` }, job ? h('span', { class: 'spinner' }) : null, label) : null,
      m.started_at ? h('span', { class: 'time' }, fmtTime(m.started_at)) : null,
      callBusy(m) ? null : moreButton(m)));
}

function noteList(items, withSnippet = false) {
  const out = [];
  let lastDay = null;
  for (const m of items) {
    if (m.started_at && dayKey(m.started_at) !== lastDay) {
      lastDay = dayKey(m.started_at);
      out.push(h('div', { class: 'day-label' }, dayWord(m.started_at)));
    }
    out.push(noteRow(m, withSnippet));
  }
  return out;
}

// -- deleting -----------------------------------------------------------------------------------
// A deleted call moves to Recently Deleted (kept 30 days). The toast's Undo, or ⌘Z while it shows, brings it back.

let undoable = null;  // { node, undo } for the toast on screen

function callBusy(m) {
  return (state.recording && state.recording.id === m.id) || !!state.jobs[m.id]
    || ['recording', 'processing', 'transcribing', 'summarizing'].includes(m.status);
}

// The ⋯ menu (or right-click menu) of a call in a list.
function callMenu(m, at) {
  openMenu(at, [
    ['notes', 'Open', () => openMeeting(m.id)],
    ['trash', 'Delete', () => deleteCall(m), callBusy(m), true],
  ]);
}

function moreButton(m) {
  return h('button', { class: 'row-more', title: 'More', 'aria-label': `More for ${m.title || m.id}`,
    onclick: (e) => { e.stopPropagation(); callMenu(m, e.currentTarget); } }, icon('more'));
}

async function deleteCall(m) {
  const wasOpen = view.kind === 'meeting' && view.id === m.id;
  const title = m.title || m.id;
  // Off the screen first, so the server's "meetings changed" doesn't try to reopen it.
  if (wasOpen) go({ kind: view.from || 'home', pinned: true });
  if (audio && audio.id === m.id) { audio.el.pause(); audio = null; }
  state.meetings = state.meetings.filter((x) => x.id !== m.id);
  renderSidebar();
  renderContent();
  try {
    await api(`/api/meetings/${encodeURIComponent(m.id)}/delete`, {});
  } catch (e) {
    toast(`Couldn't delete “${title}”: ${e.message}`, 'error');
    state.meetings = await api('/api/meetings');
    renderSidebar();
    renderContent();
    return;
  }
  undoToast(`Deleted “${title}”`, async () => {
    try {
      await api(`/api/meetings/${encodeURIComponent(m.id)}/restore`, {});
    } catch (e) {
      toast(`Couldn't undo: ${e.message}`, 'error');
      return;
    }
    state.meetings = await api('/api/meetings');
    if (wasOpen) openMeeting(m.id);
    else { renderSidebar(); renderContent(); }
  });
}

function undoToast(message, undo) {
  if (undoable) undoable.node.remove();
  const run = () => { node.remove(); undoable = null; undo(); };
  const node = h('div', { class: 'toast undo', role: 'status' }, h('span', {}, message),
    h('button', { class: 'link', onclick: run }, 'Undo'));
  $('#toasts').append(node);
  undoable = { node, undo: run };
  setTimeout(() => { node.remove(); if (undoable && undoable.node === node) undoable = null; }, 10000);
}

// -- live to-dos --------------------------------------------------------------------------------
// Your team's to-dos from the call notes (Settings → You → Your team), re-read whenever notes change.

let todos = null;              // { people, days, items } from /api/todos
let todoDays = 14;             // 14 | 30 | 0 (every call)
const todoShowDone = new Set(); // people whose done to-dos are shown

async function loadTodos() {
  try {
    todos = await api(`/api/todos?days=${todoDays || ''}`);
  } catch (e) {
    if (!todos) todos = { people: [], items: [], error: e.message };
  }
  renderSidebar();
  if (view.kind === 'todos') renderContent();
}

// The sidebar count: what's still open from the last two weeks.
function openTodos() {
  if (!todos || !todos.items) return 0;
  const since = Date.now() - 14 * 864e5;
  return todos.items.filter((i) => !i.done && new Date(i.call_at).getTime() >= since).length;
}

const localDay = (ymd) => { const [y, m, d] = ymd.split('-').map(Number); return new Date(y, m - 1, d); };

function dueChip(item) {
  if (!item.due || !/^\d{4}-\d{2}-\d{2}$/.test(item.due)) return null;
  const due = localDay(item.due), today = new Date();
  today.setHours(0, 0, 0, 0);
  if (item.done) return h('span', { class: 'due' }, `Due ${dayWord(due)}`);
  if (due < today) return h('span', { class: 'due late' }, `Overdue · ${dayWord(due)}`);
  return h('span', { class: `due${due.getTime() === today.getTime() ? ' today' : ''}` }, `Due ${dayWord(due).replace(/^(Today|Tomorrow)$/, (w) => w.toLowerCase())}`);
}

function todoRow(item, person) {
  const shared = item.people.filter((p) => p !== person);
  return h('div', { class: `todo${item.done ? ' done' : ''}` },
    h('button', { class: `todo-check${item.done ? ' on' : ''}`, role: 'checkbox', 'aria-checked': String(item.done),
      'aria-label': item.task, title: item.done ? 'Mark as not done' : 'Mark as done', onclick: () => toggleTodo(item) },
      item.done ? icon('check') : null),
    h('div', { class: 'body' },
      h('div', { class: 'task', title: item.context || '' }, item.task),
      h('div', { class: 'sub' }, dueChip(item),
        h('button', { class: 'link from', title: 'Open the call’s notes', onclick: () => openMeeting(item.call_id) }, item.call_title),
        ` · ${dayWord(item.call_at)}`, shared.length ? ` · with ${shared.join(', ')}` : '')));
}

// Due dates first (soonest first), then the newest calls.
const byUrgency = (a, b) => (a.due || '9999').localeCompare(b.due || '9999') || b.call_at.localeCompare(a.call_at);

function todoCard(person, items) {
  const open = items.filter((i) => !i.done).sort(byUrgency);
  const done = items.filter((i) => i.done);
  const showDone = todoShowDone.has(person);
  const flip = () => { showDone ? todoShowDone.delete(person) : todoShowDone.add(person); renderContent(); };
  return h('section', { class: 'todo-card' },
    h('div', { class: 'todo-head' }, h('h2', {}, person), h('span', { class: 'count' }, open.length ? `${open.length} open` : '')),
    open.length ? open.map((i) => todoRow(i, person)) : h('p', { class: 'todo-empty' }, 'Nothing open'),
    done.length ? h('button', { class: 'link todo-more', onclick: flip }, showDone ? 'Hide done' : `Show ${done.length} done`) : null,
    showDone ? done.map((i) => todoRow(i, person)) : null);
}

function renderTodos(root) {
  const range = [[14, 'Last 2 weeks'], [30, 'Last 30 days'], [0, 'All calls']];
  const head = h('div', { class: 'page-head' },
    h('div', { class: 'grow' }, h('h1', { class: 'page-title' }, 'Live To-Dos'),
      h('div', { class: 'page-sub' }, 'From your call notes, updated as each call’s notes are written')),
    h('div', { class: 'seg-ctl', title: 'Which calls’ to-dos to show' }, range.map(([days, label]) =>
      h('button', { class: todoDays === days ? 'on' : '', onclick: () => { todoDays = days; loadTodos(); } }, label))));
  if (!todos) {
    root.replaceChildren(head, h('p', { class: 'empty' }, 'Loading…'));
    return;
  }
  if (todos.error) {
    root.replaceChildren(head, notice(`Couldn't read the to-dos: ${todos.error}`));
    return;
  }
  root.replaceChildren(head,
    h('div', { class: 'todo-grid' }, todos.people.map((p) => todoCard(p, todos.items.filter((i) => i.people.includes(p))))),
    todos.people.length < 2 ? notice('Add your team in ', settingsLink('Settings'), ' to see their to-dos here too.') : null);
}

async function toggleTodo(item) {
  const done = !item.done;
  const mark = (value) => {
    for (const i of todos.items) if (i.key === item.key) i.done = value;
    renderSidebar();
    if (view.kind === 'todos') renderContent();
  };
  mark(done);
  try {
    await api('/api/todos/done', { key: item.key, done });
    if (done) undoToast(`Done: ${item.task.length > 48 ? `${item.task.slice(0, 46)}…` : item.task}`, () => toggleTodo({ ...item, done: true }));
  } catch (e) {
    mark(!done);
    toast(e.message, 'error');
  }
}

// -- home ---------------------------------------------------------------------------------------

function renderHome(root) {
  const cal = state.calendar || {};
  const now = Date.now();
  const coming = upcomingCalls();
  const firstDays = [...new Set(coming.map((e) => dayKey(e.start)))].slice(0, 2);
  const shown = coming.filter((e) => firstDays.includes(dayKey(e.start)));

  const children = [searchBar(), h('div', { class: 'section-head' }, h('span', { class: 'section-label' }, 'Coming up'),
    h('div', { class: 'section-actions' },
      h('button', { class: 'icon-btn small', title: 'Refresh calendar', 'aria-label': 'Refresh calendar',
        onclick: (e) => refreshCalendar(e.currentTarget) }, icon('refresh')),
      h('button', { class: 'link', onclick: () => go({ kind: 'upcoming', pinned: true }) }, 'See all')))];
  children.push(calendarProblem(cal)
    || (shown.length ? eventCard(shown, now, 4)
      : notice(`No calls with other people in the next ${cal.days_ahead || 7} days.`)));

  children.push(h('div', { class: 'section-head' }, h('span', { class: 'section-label' }, 'Notes'),
    state.meetings.length > 25 ? h('button', { class: 'link', onclick: () => go({ kind: 'calls' }) }, 'All notes') : null));
  children.push(...(state.meetings.length ? noteList(state.meetings.slice(0, 25))
    : [notice('No calls yet. Click ', h('b', {}, 'New recording'), ' (the pencil at the top left) or record a call from Coming up.')]));
  root.replaceChildren(...children);
}

// -- upcoming -----------------------------------------------------------------------------------

function renderUpcoming(root) {
  const cal = state.calendar || {};
  const now = Date.now();
  const todayKey = dayKey(new Date());
  // Invites with someone else on them: the rest of today (incl. earlier calls) plus the days ahead.
  const events = (cal.events || []).filter((e) => !e.all_day && (e.others || []).length
    && (new Date(e.end).getTime() > now || dayKey(e.start) === todayKey));
  const toRecord = events.filter((e) => e.is_call && new Date(e.end).getTime() > now && e.plan !== 'skip').length;

  const head = h('div', { class: 'page-head' },
    h('div', { class: 'grow' }, h('h1', { class: 'page-title' }, 'Upcoming'),
      h('div', { class: 'page-sub' }, `Calls with other people over the next ${cal.days_ahead || 7} days`
        + (toRecord ? ` · ${toRecord} to record` : ''))),
    h('div', { class: 'seg-ctl', title: 'What happens when a call starts, unless you choose otherwise for that call' },
      h('button', { class: !cal.auto_record ? 'on' : '', onclick: () => setAutoRecord(false) }, 'Remind me'),
      h('button', { class: cal.auto_record ? 'on rec' : '', onclick: () => setAutoRecord(true) }, 'Record automatically')),
    h('button', { class: 'icon-btn', title: 'Refresh calendar', 'aria-label': 'Refresh calendar',
      onclick: (e) => refreshCalendar(e.currentTarget) }, icon('refresh')));

  root.replaceChildren(head, calendarProblem(cal)
    || (events.length ? eventCard(events, now) : notice(`No calls with other people in the next ${cal.days_ahead || 7} days.`)));
}

// -- notes for an upcoming call -------------------------------------------------------------------
// Open a call on Upcoming to write notes for it ahead of time: an agenda, questions to ask. When it's recorded,
// they're where the call's notes start (My notes), so they're in front of you during the call, and Claude writes
// the summary around them.

const findEvent = (id) => (state.calendar.events || []).find((e) => e.id === id);
const prepStatus = h('span', { class: 'saved' });
let prepSave = null;  // notes typed but not saved yet: { id, text, editor, timer }

function openEvent(id) {
  const ev = findEvent(id);
  if (!ev) {
    toast('That call isn’t on your calendar any more.', 'error');
    return go({ kind: 'upcoming', pinned: true });
  }
  if (ev.recording_now || ev.meeting_id) return openEventRow(ev);
  go({ kind: 'event', id, from: view.kind === 'home' ? 'home' : 'upcoming' });
  $('#content .editor.mynotes')?.focus();
}

function renderEvent(root) {
  let ev = findEvent(view.id);
  if (!ev) {  // moved to another time, which changes its id: it's the one call left with the rest of that id
    const base = (id) => id.slice(0, id.lastIndexOf('@'));
    const same = (state.calendar.events || []).filter((e) => base(e.id) === base(view.id));
    if (same.length === 1) {
      ev = same[0];
      view.id = ev.id;
      history.replaceState(null, '', pageHash());
      const kept = root.querySelector('.editor.mynotes');
      if (kept) kept.dataset.event = ev.id;
    }
  }
  const back = h('button', { class: 'crumb', onclick: () => go({ kind: view.from || 'upcoming', pinned: true }) },
    icon('back'), view.from === 'home' ? 'Home' : 'Upcoming');
  if (!ev) {
    delete root.dataset.event;
    return root.replaceChildren(h('div', { class: 'note-top' }, back), notice('That call isn’t on your calendar any more.'));
  }
  if (ev.recording_now) return go({ kind: 'live' });

  const conf = conferenceName(ev.conference_url);
  const head = [
    h('div', { class: 'note-top' }, back, h('div', { class: 'note-actions' }, prepStatus, eventSide(ev, Date.now()))),
    h('h1', { class: 'note-title' }, ev.title || 'Untitled call'),
    h('div', { class: 'chips' },
      chip('calendar', `${dayWord(ev.start)} · ${fmtTime(ev.start)} – ${fmtTime(ev.end)}`),
      (ev.others || []).length ? chip('people', shortNames(ev.others, 3)) : null,
      conf ? chip('video', '', h('a', { href: ev.conference_url, target: '_blank' }, conf)) : null),
    h('p', { class: 'prep-hint' }, ev.meeting_id ? 'These notes went into the call’s notes when it was recorded.'
      : 'These go into the call’s notes when it’s recorded, so they’re in front of you during the call.'),
  ];
  // Calendar updates redraw the top of the page; the notepad you're typing in stays put.
  if (root.dataset.event === ev.id && root.querySelector('.prep-head')) return root.querySelector('.prep-head').replaceChildren(...head);
  root.dataset.event = ev.id;
  prepStatus.textContent = '';
  const editor = noteEditor({
    value: (prepSave && prepSave.id === ev.id ? prepSave.text : ev.my_notes) || '',
    placeholder: 'Write notes for this call: an agenda, questions to ask, what you want out of it…',
    onChange: (text) => queuePrep(editor.dataset.event, text, editor),
  });
  editor.classList.add('mynotes');
  editor.dataset.event = ev.id;
  editor.saved = editor.getValue();
  root.replaceChildren(h('div', { class: 'prep-head' }, ...head), editor, editorHint());
}

function queuePrep(id, text, editor) {
  if (prepSave && prepSave.id !== id) flushPrep();  // another call's notes, still to save
  if (prepSave) clearTimeout(prepSave.timer);
  prepStatus.textContent = '';
  prepSave = { id, text, editor, timer: setTimeout(flushPrep, 700) };
}

async function flushPrep() {
  const p = prepSave;
  if (!p) return;
  clearTimeout(p.timer);
  prepSave = null;
  try {
    await api('/api/upcoming/notes', { id: p.id, text: p.text });
    const ev = findEvent(p.id);
    if (ev) ev.my_notes = p.text;
    p.editor.saved = p.text;
    if (!prepSave && p.editor.isConnected) prepStatus.textContent = 'Saved';
  } catch (e) {
    toast(`Couldn’t save your notes: ${e.message}`, 'error');
  }
}

async function setAutoRecord(on) {
  try {
    state.settings = await api('/api/settings', { calendar: { auto_record: on } });
    state.calendar = await api('/api/calendar/refresh', {});
    renderAll();
  } catch (e) { toast(e.message, 'error'); }
}

// The refresh buttons: Calendar pulls new invites from Google first (a few seconds), then the list re-reads it.
async function refreshCalendar(button) {
  if (button) { button.classList.add('spinning'); button.disabled = true; }
  try {
    state.calendar = await api('/api/calendar/refresh', { sync: true });
    renderAll();
    toast(state.calendar.error ? `Calendar: ${state.calendar.error}` : 'Calendar refreshed', state.calendar.error ? 'error' : 'info');
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    if (button) { button.classList.remove('spinning'); button.disabled = false; }
  }
}

// -- all notes / search -------------------------------------------------------------------------

let searchTimer;
let searchSince = '';  // '' for any time, or a number of days back
let searchTerms = [];  // the words the last search matched, for highlighting
let asking = null;     // Claude's answer to the current search: { question, controller, status, step, text, html, error }
let recentSearches = null;  // [{ q, at, answer?, answered_at?, html }], newest first (null: not loaded yet)
let rememberTimer;
const sameQuery = (a, b) => a.trim().replace(/\s+/g, ' ').toLowerCase() === b.trim().replace(/\s+/g, ' ').toLowerCase();

// The recent searches, from Call Recorder (it keeps them, with Claude's answers, in ~/.callrec/searches.json).
async function loadRecents() {
  try { recentSearches = await api('/api/searches'); } catch { recentSearches = []; }  // (a server older than this: none)
  if (view.kind === 'calls') renderCalls($('#content'));
}

// A search you stayed on goes into the recent ones (Call Recorder folds "conf" → "confirmed" into one).
function rememberSearch(query) {
  clearTimeout(rememberTimer);
  rememberTimer = setTimeout(() => {
    if (search !== query) return;
    api('/api/searches', { q: query }).then(() => { recentSearches = null; }).catch(() => {});
  }, 1500);
}

function forgetSearch(q) {
  api('/api/searches/forget', q ? { q } : { all: true }).then(loadRecents).catch((e) => toast(e.message, 'error'));
}

function recentList() {
  if (!recentSearches || !recentSearches.length) return null;
  const when = (iso) => `${dayWord(iso)} · ${fmtTime(iso)}`;
  return h('div', { class: 'recent-searches' },
    h('div', { class: 'recent-head' }, h('span', { class: 'grow' }, 'Recent searches'),
      h('button', { class: 'link muted', onclick: () => forgetSearch(null) }, 'Clear')),
    ...recentSearches.slice(0, 8).map((item) => h('div', { class: 'recent-row' },
      h('button', { class: 'recent-go', title: item.answer ? 'Search again, with Claude’s saved answer' : 'Search again',
        onclick: () => { const input = $('#content .search-input'); if (input) input.value = item.q; runSearch(item.q); } },
        icon(item.answer ? 'spark' : 'search'), h('span', { class: 'recent-q' }, item.q),
        h('span', { class: 'recent-when' }, item.answer ? `Answered ${when(item.answered_at || item.at)}` : when(item.at))),
      h('button', { class: 'icon-btn small', title: 'Remove from recent searches', 'aria-label': `Remove “${item.q}”`,
        onclick: () => forgetSearch(item.q) }, icon('close')))));
}

function searchBar() {
  const input = h('input', { type: 'search', class: 'search-input', value: search, autocomplete: 'off', spellcheck: 'false',
    placeholder: 'Search your calls, or ask Claude a question about them', 'aria-label': 'Search your calls' });
  input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    if (view.kind !== 'calls') return runSearch(input.value);  // typing on Home goes straight to results
    searchTimer = setTimeout(() => runSearch(input.value), 180);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { clearTimeout(searchTimer); runSearch(input.value); askClaude(search); }
    if (e.key === 'Escape') { input.value = ''; clearTimeout(searchTimer); runSearch(''); }
  });
  return h('label', { class: 'search-bar' }, icon('search'), input, h('kbd', {}, '⌘K'));
}

function focusSearch(select) {
  const input = $('#content .search-input');
  if (!input) return;
  input.focus();
  if (select) input.select();
  else input.setSelectionRange(input.value.length, input.value.length);
}

function openSearch() {
  if (view.kind !== 'calls') go({ kind: 'calls' });
  focusSearch(true);
}

function runSearch(text) {
  search = text.trim();
  if (asking && asking.question !== search && asking.status === 'asking') asking.controller.abort();  // stop paying for a stale question
  if (view.kind !== 'calls') { go({ kind: 'calls' }); focusSearch(); return; }
  history.replaceState(null, '', pageHash() || location.pathname);
  renderCalls($('#content'));
}

// -- Ask Claude: it searches and reads your calls with the same tools it has in Claude Code, then answers.

async function askClaude(question) {
  question = question.trim();
  if (!question) return;
  if (asking && asking.status === 'asking') asking.controller.abort();
  const mine = { question, controller: new AbortController(), status: 'asking', step: 'Reading your question', text: '', html: '', error: '' };
  asking = mine;
  renderAsk();
  try {
    const res = await fetch('/api/ask', {
      method: 'POST', signal: mine.controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-CallRec': '1' }, body: JSON.stringify({ question }),
    });
    await readAnswer(res, mine, () => { if (asking === mine) renderAsk(); });
    if (mine.status === 'done') recentSearches = null;  // it's saved with the recent searches now
  } catch (e) {
    if (e.name === 'AbortError') return;
    mine.status = 'error';
    mine.error = e.message === 'Not found'  // a server started before this existed
      ? 'Asking Claude needs Call Recorder to restart: quit it from the menu bar and open it again.' : e.message;
  }
  if (asking === mine) renderAsk();
}

// Follow /api/ask's stream into `mine` (step, text, then status done + html + answer, or error), calling update().
async function readAnswer(res, mine, update) {
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl);
      buffer = buffer.slice(nl + 1);
      if (!line.trim()) continue;
      const ev = JSON.parse(line);
      if (ev.type === 'step') { mine.step = ev.text; mine.text = ''; }  // text before a search was thinking aloud
      else if (ev.type === 'text') { mine.text += ev.text; mine.step = 'Writing the answer'; }
      else if (ev.type === 'done') { mine.status = 'done'; mine.html = ev.html; mine.answer = ev.answer; }
      else if (ev.type === 'error') { mine.status = 'error'; mine.error = ev.message; }
    }
    update();
  }
  if (mine.status === 'asking') { mine.status = 'error'; mine.error = 'Claude stopped without answering. Try again.'; }
}

function renderAsk() {
  const box = $('#ask-box');
  if (!box) return;
  if (!search) return box.replaceChildren();
  let a = asking && asking.question === search ? asking : null;
  const saved = !a && (recentSearches || []).find((i) => i.answer && sameQuery(i.q, search));
  if (saved) a = { question: search, status: 'done', html: saved.html, saved: saved.answered_at || saved.at };
  if (!a) {
    return box.replaceChildren(h('button', { class: 'ask-prompt', onclick: () => askClaude(search) },
      icon('spark'), h('span', { class: 'grow' }, h('b', {}, 'Ask Claude'), ` · “${search}”`), h('kbd', {}, 'Enter')));
  }
  let body = null;
  if (a.status === 'done') {
    body = h('div', { class: 'doc ask-answer', html: a.html });
    wireCallLinks(body);
  } else if (a.status === 'error') {
    body = h('div', { class: 'ask-error' }, a.error);
  } else if (a.text) {
    body = h('div', { class: 'doc ask-answer streaming', html: streamingHtml(a.text) });
    wireCallLinks(body);
  }
  box.replaceChildren(h('div', { class: `ask-card ${a.status}` },
    h('div', { class: 'ask-head' }, icon('spark'), h('b', {}, 'Claude'),
      a.status === 'asking' ? h('span', { class: 'ask-step' }, h('span', { class: 'spinner' }), a.step)
        : h('span', { class: 'grow ask-saved' }, a.saved ? `Saved answer from ${dayWord(a.saved).toLowerCase()} at ${fmtTime(a.saved)}` : ''),
      a.status === 'asking' ? null : h('button', { class: 'link', onclick: () => askClaude(a.question) }, 'Ask again')),
    body));
}

// Claude links calls as call:<id> and moments as call:<id>@39:10; open them in the app. A source at the end of a
// point ([39:10] or [call]) shows as a small numbered chip, as Granola does: one number per call, or per moment
// in an answer about one call (`byMoment`). Hovering a chip says where it goes and what was said there.
const CITE_LABEL = /^\s*(?:[~≈]?\d{1,2}:\d{2}(?::\d{2})?|\d{1,2}|call|source)\s*$/i;

function wireCallLinks(root, byMoment = false) {
  const numbers = new Map();
  for (const link of root.querySelectorAll('a[href^="call:"]')) {
    const [id, at] = decodeURIComponent(link.getAttribute('href').slice(5)).split('@');
    const call = state.meetings.find((m) => m.id === id) || state.meetings.find((m) => m.id.slice(0, 15) === id.slice(0, 15));
    const cite = CITE_LABEL.test(link.textContent);
    link.classList.add(cite ? 'cite' : 'call-link');
    link.setAttribute('href', '#');
    if (cite) {
      const key = byMoment ? `${id}@${at || ''}` : id;
      if (!numbers.has(key)) numbers.set(key, numbers.size + 1);
      link.textContent = numbers.get(key);
    }
    if (!call) { link.classList.add('missing'); link.title = 'This call isn’t in your recordings'; continue; }
    const seconds = at ? at.replace(/[^\d:]/g, '').split(':').reduce((sum, part) => sum * 60 + Number(part), 0) : undefined;  // "~05:32" too
    if (cite) link.title = citeTitle(call, at, seconds);
    else if (at) link.prepend(icon('play'));
    link.addEventListener('click', (e) => {
      e.preventDefault();
      if (view.kind === 'meeting' && detail && detail.meta.id === call.id && Number.isFinite(seconds)) jumpTo(seconds);  // this call: just go there
      else openMeeting(call.id, 'notes', Number.isFinite(seconds) ? seconds : undefined, searchTerms);
    });
  }
}

// What a chip points to: the call (unless it's the one open) and the moment, with what was said then if this
// call's transcript is on the page.
function citeTitle(call, at, seconds) {
  const here = detail && detail.meta.id === call.id;
  const where = [here ? null : `${call.title || call.id}, ${dayWord(call.started_at)}`, at ? at.replace(/^[~≈]/, '') : null].filter(Boolean).join(' · ');
  const seg = here && Number.isFinite(seconds) ? (detail.transcript || []).filter((s) => s.start <= seconds + 0.5).pop() : null;
  if (!seg) return where || 'Open the call';
  const said = seg.text.length > 160 ? `${seg.text.slice(0, 157).trimEnd()}…` : seg.text;
  return `${where}\n${seg.speaker}: “${said}”`;
}

// An answer still being written, as HTML: paragraphs, bullets, bold, and its sources as links (an unfinished
// link at the end is left out until it's complete).
function streamingHtml(md) {
  const links = [];
  const text = md.replace(/\[[^\]\n]*(?:\]\([^)\n]*)?$/, '')
    .replace(/\[([^\]\n]+)\]\((call:[^)\n]+)\)/g, (m, label, href) => `\u0000${links.push([label, href]) - 1}\u0000`);
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const { toHtml } = window.notesMarkdown;
  return text.split(/\n\s*\n/).filter((block) => block.trim()).map((block) => (/^\s*([-*+]|\d+[.)])\s/m.test(block) ? toHtml(block)
    : toHtml(block.trim().replace(/\s*\n\s*/g, ' ')).replace(/^<div>/, '<p>').replace(/<\/div>$/, '</p>'))).join('')
    .replace(/\u0000(\d+)\u0000/g, (m, i) => `<a href="${esc(links[i][1].replace(/ /g, '%20'))}">${esc(links[i][0])}</a>`);
}

// Wrap the words that matched (whole words starting with a search term) in <mark>.
function highlight(text, terms) {
  if (!text || !terms || !terms.length) return [text || ''];
  const words = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  const out = [];
  let last = 0;
  for (const m of text.matchAll(new RegExp(`\\b(?:${words})[\\w'’-]*`, 'gi'))) {
    out.push(text.slice(last, m.index), h('mark', {}, m[0]));
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

// Up to `max` passages per call: notes lines first, but at least half the room for moments in the transcript.
function pickHits(matches, max) {
  const notes = matches.filter((m) => m.kind === 'notes');
  const talk = matches.filter((m) => m.kind === 'transcript');
  const nTalk = Math.min(talk.length, Math.max(max - notes.length, Math.ceil(max / 2)));
  return [...notes.slice(0, max - nTalk), ...talk.slice(0, nTalk)];
}

const MAX_HITS = 4;
function resultCard(r, terms) {
  const others = (r.people || []).filter((p) => p !== state.settings.my_name);
  const meta = [r.started_at ? `${dayWord(r.started_at)} · ${fmtTime(r.started_at)}` : '',
    others.length ? shortNames(others, 3) : '', r.duration ? fmtLength(r.duration) : ''].filter(Boolean).join(' · ');
  const hits = pickHits(r.matches, MAX_HITS).map((m) => (m.kind === 'transcript'
    ? h('button', { class: 'hit', title: 'Open the transcript here', onclick: () => openMeeting(r.id, 'notes', m.start, terms) },
      h('span', { class: 'hit-where' }, icon('play'), h('span', {}, `${m.approx ? '~' : ''}${fmtDur(m.start)}${m.speaker ? ` · ${m.speaker}` : ''}`)),
      h('span', { class: 'hit-text' }, highlight(m.text, terms)))
    : h('button', { class: 'hit', onclick: () => openMeeting(r.id) },
      h('span', { class: 'hit-where' }, icon('notes'), h('span', {}, m.section)),
      h('span', { class: 'hit-text' }, highlight(m.text, terms)))));
  if (!hits.length && r.summary) hits.push(h('div', { class: 'hit-text alone' }, highlight(r.summary, terms)));
  const more = r.matches.length - MAX_HITS;
  return h('div', { class: 'result' },
    h('button', { class: 'result-head', onclick: () => openMeeting(r.id) },
      h('span', { class: 'result-title' }, highlight(r.title, terms)),
      meta ? h('span', { class: 'result-meta' }, meta) : null),
    ...hits,
    more > 0 ? h('button', { class: 'link result-more', onclick: () => openMeeting(r.id) }, `${more} more in this call`) : null);
}

async function renderCalls(root) {
  let results = $('#search-results', root);
  if (!results) {  // build the page once; searching after that only refreshes the results, so typing keeps focus
    results = h('div', { id: 'search-results' });
    root.replaceChildren(
      h('div', { class: 'page-head' }, h('div', { class: 'grow' },
        h('h1', { class: 'page-title', id: 'search-title' }), h('div', { class: 'page-sub', id: 'search-sub' }))),
      searchBar(), h('div', { class: 'search-filters', id: 'search-filters' }), h('div', { id: 'ask-box' }), results);
  }
  const query = search;
  const input = $('.search-input', root);
  if (input.value.trim() !== query && document.activeElement !== input) input.value = query;
  $('#search-title', root).textContent = query ? 'Search' : 'All notes';
  $('#search-filters', root).replaceChildren(...(query ? [['', 'Any time'], ['7', 'Past week'], ['30', 'Past month'], ['90', 'Past 3 months']]
    .map(([days, label]) => h('button', { class: `filter${searchSince === days ? ' on' : ''}`, onclick: () => { searchSince = days; renderCalls(root); } }, label)) : []));
  const sub = $('#search-sub', root);
  renderAsk();
  if (!query) {
    sub.textContent = `${state.meetings.length} recorded call${state.meetings.length === 1 ? '' : 's'}`;
    if (recentSearches === null) loadRecents();
    results.replaceChildren(...[recentList()].filter(Boolean), ...(state.meetings.length ? noteList(state.meetings, true)
      : [notice('No calls yet. Recorded calls and their notes collect here.')]));
    return;
  }
  sub.textContent = 'Searching titles, people, notes and transcripts…';
  const since = searchSince ? new Date(Date.now() - Number(searchSince) * 864e5).toISOString().slice(0, 10) : '';
  let found;
  try {
    found = await api(`/api/search?q=${encodeURIComponent(query)}${since ? `&since=${since}` : ''}`);
  } catch (e) {
    results.replaceChildren(notice(e.message === 'Not found'  // a server started before search existed
      ? 'Search needs Call Recorder to restart: quit it from the menu bar and open it again.' : e.message));
    return;
  }
  if (query !== search || view.kind !== 'calls' || !results.isConnected) return;  // a newer search (or page) took over
  searchTerms = found.terms;
  rememberSearch(query);
  const n = found.results.length;
  sub.textContent = n ? `${n} call${n === 1 ? '' : 's'} match, best first` : '';
  results.replaceChildren(...(n ? found.results.map((r) => resultCard(r, found.terms))
    : [notice(`Nothing matches “${query}”${searchSince ? ' in that time' : ''}. Try a name, a project or a topic.`)]));
}

// -- live recording -----------------------------------------------------------------------------

function chip(name, text, extra) {
  return h('span', { class: 'chip' }, icon(name), text ? h('span', {}, text) : null, extra || null);
}

async function renderLive(root) {
  const rec = state.recording;
  root.dataset.live = rec.id;
  const ev = rec.event;
  const title = h('input', { class: 'note-title', value: rec.title, 'aria-label': 'Call title', placeholder: 'Untitled call' });
  title.addEventListener('change', () => api('/api/record/title', { title: title.value }).then(loadState));
  const chips = h('div', { class: 'chips' },
    chip('calendar', ev ? `${dayWord(rec.started_at)} · ${fmtTime(ev.start)} – ${fmtTime(ev.end)}` : `${dayWord(rec.started_at)} · ${fmtTime(rec.started_at)}`),
    ev && (ev.attendees || []).length ? chip('people', shortNames(ev.attendees, 3)) : null,
    ev && ev.conference_url ? chip('video', '', h('a', { href: ev.conference_url, target: '_blank' }, conferenceName(ev.conference_url))) : null);
  const saved = h('span', { class: 'saved' });
  let timer;
  const notes = noteEditor({
    placeholder: 'Write notes… Claude builds the summary around what you jot down here.',
    onChange: (text) => {
      saved.textContent = '';
      clearTimeout(timer);
      timer = setTimeout(async () => {
        await api('/api/record/notes', { text });
        saved.textContent = 'Saved';
      }, 700);
    },
  });
  notes.id = 'live-notes';
  const top = h('div', { class: 'note-top' },
    h('button', { class: 'crumb', onclick: () => go({ kind: 'home', pinned: true }) }, icon('back'), 'Home'), saved);
  root.replaceChildren(top, title, chips, notes, editorHint());

  $('#tpanel-extra').textContent = state.settings.live_transcription === false ? '' : 'Live · a few seconds behind';
  $('#tpanel-body').replaceChildren(h('div', { class: 'empty', id: 'live-empty' }, state.settings.live_transcription === false
    ? 'Live transcription is off. The full transcript is made when you stop.'
    : 'Listening… what’s said appears here a few seconds behind.'));
  renderDock();
  const { segments, notes: existing } = await api('/api/record/live');
  if (!notes.getValue()) notes.setValue(existing || '');  // unless you've started typing already
  segments.forEach(addSegment);
  notes.focus();
}

function bubble(seg, onclick) {
  const me = seg.track === 'mic' || seg.speaker === state.settings.my_name;
  const named = !me || seg.speaker !== state.settings.my_name;  // your own lines need no label, "Mic" does
  return h('div', { class: `bubble ${me ? 'me' : 'them'}${onclick ? ' seekable' : ''}`, 'data-id': seg.id, 'data-start': seg.start, onclick,
    style: me ? null : `--spk: var(--spk-${speakerColor(seg.speaker_id || seg.speaker)})` },
    h('span', { class: 't' }, named && seg.speaker ? [h('b', { class: 'who' }, seg.speaker), ' · '] : null,
      `${seg.approx ? '~' : ''}${fmtDur(seg.start)}`),  // "~": an imported transcript's estimated time
    h('div', { class: 'text' }, seg.text));
}

// A stable color per voice, so you can follow who's talking.
function speakerColor(key) {
  const n = /^S(\d+)$/.exec(key || '');
  if (n) return (Number(n[1]) - 1) % 8;
  let hash = 0;
  for (const ch of String(key || '')) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return hash % 8;
}

function addSegment(seg) {
  if (view.kind !== 'live') return;
  const box = $('#tpanel-body');
  $('#live-empty')?.remove();
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  const node = bubble(seg);
  // keep chronological order (the two sides are transcribed independently), above the lines still being spoken
  const after = [...box.querySelectorAll('.bubble:not(.partial)')].find((n) => Number(n.dataset.start) > seg.start)
    || box.querySelector('.bubble.partial');
  after ? box.insertBefore(node, after) : box.append(node);
  if (stick) box.scrollTop = box.scrollHeight;
}

// The line someone is still saying: shown lighter at the bottom, rewritten as the words come in, and
// replaced by the finished line when they pause.
function showPartial(seg) {
  if (view.kind !== 'live') return;
  const box = $('#tpanel-body');
  $('#live-empty')?.remove();
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  const node = bubble({ ...seg, id: `partial-${seg.track}` });
  node.classList.add('partial');
  node.dataset.track = seg.track;
  const old = box.querySelector(`.bubble.partial[data-track="${seg.track}"]`);
  old ? old.replaceWith(node) : box.append(node);
  if (stick) box.scrollTop = box.scrollHeight;
}

// -- dock + transcript panel ----------------------------------------------------------------------

function renderDock() {
  const dock = $('#dock');
  let items = null;
  if (view.kind === 'live' && state.recording) {
    items = [
      h('button', { class: `bars-btn${transcriptOpen ? ' open' : ''}`, onclick: toggleTranscript, title: 'Show the live transcript' }, barsEl('live'), 'Transcript'),
      h('span', { class: `state${state.recording.paused ? ' paused' : ''}` },
        h('b', { id: 'dock-timer' }, fmtDur(elapsed(state.recording))), state.recording.paused ? ' · Paused' : ' · Recording'),
      state.recording.paused
        ? h('button', { class: 'primary', onclick: resumeRecording, title: 'Carry on recording this call' }, icon('record'), 'Resume')
        : h('button', { class: 'secondary', onclick: pauseRecording, title: 'Stop recording for now: nothing is recorded until you resume' }, icon('pause'), 'Pause'),
      h('button', { class: 'dark', onclick: stopRecording }, icon('stop'), 'Stop')];
  } else if (view.kind === 'meeting' && detail && detail.meta.id === view.id) {
    const hasTranscript = (detail.transcript || []).length > 0;
    if (detail.has_audio && (!audio || audio.id !== detail.meta.id)) {
      audio = { id: detail.meta.id, el: h('audio', { controls: true, preload: 'metadata', src: `/api/meetings/${encodeURIComponent(detail.meta.id)}/audio` }) };
    }
    items = [
      hasTranscript ? h('button', { class: `bars-btn${transcriptOpen ? ' open' : ''}`, onclick: toggleTranscript, title: 'Show the transcript' }, barsEl('static'), 'Transcript') : null,
      detail.has_audio ? audio.el : null,
      detail.can_record ? recordOntoButton(detail.meta) : null,
      askBar];
  }
  // Leaving a note takes its player out of the page; don't let it keep talking.
  if (audio && !(view.kind === 'meeting' && detail && audio.id === detail.meta.id)) audio.el.pause();
  dock.hidden = !items;
  const typing = document.activeElement === askInput && [askInput.selectionStart, askInput.selectionEnd];
  if (items) dock.replaceChildren(...items.filter(Boolean));
  if (typing && askInput.isConnected) { askInput.focus(); askInput.setSelectionRange(...typing); }  // redrawn while you type
  renderAskBar();
  $('#tpanel').hidden = !items || !transcriptOpen || (view.kind === 'meeting' && !(detail.transcript || []).length);
  reportWatching();
}

// Tell Call Recorder whether the live transcript is on screen: the line still being spoken is only worked out
// while it is (that keeps the graphics chip busy). Said again every 10 s while it's showing.
let watchingLive = false;
function reportWatching(again = false) {
  const now = view.kind === 'live' && transcriptOpen && document.visibilityState === 'visible';
  if (now === watchingLive && !(now && again)) return;
  watchingLive = now;
  api('/api/live/watching', { on: now }).catch(() => {});
}
document.addEventListener('visibilitychange', () => reportWatching());
setInterval(() => reportWatching(true), 10e3);

function toggleTranscript() {
  transcriptOpen = !transcriptOpen;
  renderDock();
  if (transcriptOpen) { const box = $('#tpanel-body'); if (view.kind === 'live') box.scrollTop = box.scrollHeight; }
}
$('#tpanel-close').addEventListener('click', () => { transcriptOpen = false; renderDock(); });

// Group consecutive lines from the same side into one bubble.
function paragraphs(segs, gap = 2.5) {
  const out = [];
  for (const s of segs) {
    const last = out[out.length - 1];
    if (last && last.speaker === s.speaker && s.start - last.end <= gap) { last.text += ` ${s.text}`; last.end = Math.max(last.end, s.end); }
    else out.push({ ...s, track: [state.settings.my_name, 'Mic'].includes(s.speaker) ? 'mic' : 'system' });
  }
  return out;
}

function fillMeetingTranscript() {
  const segs = detail.transcript || [];
  $('#tpanel-extra').textContent = detail.has_audio ? 'Click a line to play from there'
    : detail.meta.times_estimated ? `From ${detail.meta.transcript_source || 'an import'} · times are approximate` : '';
  const seek = (s) => () => {
    if (!audio || audio.id !== detail.meta.id) return;
    audio.el.currentTime = s.start;
    audio.el.play();
  };
  $('#tpanel-body').replaceChildren(...(segs.length ? paragraphs(segs).map((s) => bubble(s, detail.has_audio ? seek(s) : null))
    : [h('div', { class: 'empty' }, 'No transcript yet.')]));
}

// -- one call -----------------------------------------------------------------------------------

async function openMeeting(id, tab, at, terms) {
  const from = view.kind === 'meeting' ? view.from : (view.kind === 'live' ? 'home' : view.kind);
  const sameMeeting = view.id === id;
  view = { kind: 'meeting', id, from, tab: tab || (sameMeeting ? view.tab : 'notes') };
  try {
    detail = await api(`/api/meetings/${encodeURIComponent(id)}`);
  } catch (e) {
    toast(e.message, 'error');
    return;
  }
  if (view.kind !== 'meeting' || view.id !== id) return;
  if (!sameMeeting) askInput.value = '';  // a question you were typing was about the other call
  syncHash();
  closeMenu();
  renderSidebar();
  renderContent();
  fillMeetingTranscript();
  renderDock();
  if (!sameMeeting) $('.main').scrollTop = 0;
  if (at !== undefined) jumpTo(at, terms);
}

// Open the transcript at a moment (a search result), mark the words searched for, and cue the audio there.
function jumpTo(at, terms) {
  transcriptOpen = true;
  renderDock();
  const lines = [...$('#tpanel-body').querySelectorAll('.bubble')];
  const line = lines.filter((b) => Number(b.dataset.start) <= at + 0.5).pop() || lines[0];
  if (line) {
    const text = $('.text', line);
    if (terms && terms.length) text.replaceChildren(...highlight(text.textContent, terms));
    ($('mark', text) || line).scrollIntoView({ block: 'center' });  // long paragraphs: show the match itself
    line.classList.add('flash');
  }
  if (audio && audio.id === detail.meta.id) {
    const cue = () => { audio.el.currentTime = at; };
    if (audio.el.readyState >= 1) cue();
    else audio.el.addEventListener('loadedmetadata', cue, { once: true });
  }
}

// -- asking in the dock -------------------------------------------------------------------------------
// The bar at the bottom of a call asks Claude about that call: it reads the call's notes, your notes and the
// transcript, and the answer appears in the Ask tab (with links to the moments it used), kept with the call.

const askInput = h('input', { type: 'text', class: 'dock-ask-input', placeholder: 'Ask about this call…', autocomplete: 'off',
  'aria-label': 'Ask Claude about this call' });
const askEarlier = h('div', { class: 'ask-earlier', hidden: true });
const askBar = h('div', { class: 'dock-ask', title: 'Claude answers from this call’s notes and transcript (⌘F)', onclick: () => askInput.focus() },
  icon('spark'), h('span', { class: 'spinner' }), askInput, askEarlier);

// Clicking into the bar lists the questions already asked about this call; picking one shows its saved answer.
function renderEarlier() {
  const past = view.kind === 'meeting' && detail ? detail.questions || [] : [];
  const show = document.activeElement === askInput && !askInput.value.trim() && past.length > 0;
  askEarlier.hidden = !show;
  if (!show) return;
  const keep = (e) => e.preventDefault();  // (keeps the bar focused while you click)
  askEarlier.replaceChildren(
    h('div', { class: 'ask-earlier-head' }, h('span', { class: 'grow' }, `Asked about this call · ${past.length}`),
      h('button', { class: 'link', onmousedown: keep, onclick: (e) => { e.stopPropagation(); showAnswer(past.length - 1, false); } }, 'See all')),
    ...past.map((q, i) => [q, i]).reverse().slice(0, 8).map(([q, i]) =>
      h('button', { class: 'ask-earlier-q', title: 'Show the answer', onmousedown: keep,
        onclick: (e) => { e.stopPropagation(); showAnswer(i); } }, icon('spark'), h('span', {}, q.q))));
}
askInput.addEventListener('focus', renderEarlier);
askInput.addEventListener('input', renderEarlier);
askInput.addEventListener('blur', () => { askEarlier.hidden = true; });

// The Ask tab, at the `i`th saved answer (highlighted for a moment).
function showAnswer(i, flash = true) {
  askInput.blur();
  if (view.tab !== 'ask') { view.tab = 'ask'; renderContent(); }
  const node = document.querySelectorAll('#call-ask .qa')[i];
  if (!node) return;
  node.scrollIntoView({ block: 'start', behavior: 'smooth' });
  if (flash) { node.classList.remove('flash'); void node.offsetWidth; node.classList.add('flash'); }
}
askInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.isComposing) {
    e.preventDefault();
    const question = askInput.value.trim();
    if (!question || !detail || view.kind !== 'meeting' || (callAsk && callAsk.status === 'asking')) return;
    askInput.value = '';
    if (view.tab !== 'ask') { view.tab = 'ask'; renderContent(); }
    askCall(detail.meta.id, question);
    $('#call-ask .qa:last-child')?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  } else if (e.key === 'Escape') {
    e.stopPropagation();  // not the page's Escape (which closes the transcript)
    if (!askEarlier.hidden && !askInput.value) { askEarlier.hidden = true; return; }
    askInput.value = '';
    askInput.blur();
  }
});

// While Claude is answering, the bar says so (and waits to send the next question).
function renderAskBar() {
  const busy = !!(callAsk && callAsk.status === 'asking' && detail && callAsk.id === detail.meta.id);
  askBar.classList.toggle('busy', busy);
  askInput.placeholder = busy ? 'Claude is answering…' : detail && (detail.questions || []).length ? 'Ask a follow-up…' : 'Ask about this call…';
}

function renderMeeting(root) {
  const { meta, summary } = detail;
  const job = state.jobs[meta.id] || detail.job;
  const people = (summary && summary.participants && summary.participants.length
    ? summary.participants : (meta.attendees || []).map((a) => a.name || a.email)).filter(Boolean);
  const busy = !!job || ['processing', 'transcribing', 'summarizing'].includes(meta.status);
  const back = { upcoming: 'Upcoming', calls: search ? 'Search' : 'All notes' }[view.from] || 'Home';

  const title = h('input', { class: 'note-title', value: meta.title || meta.id, 'aria-label': 'Call title' });
  title.addEventListener('change', () => api(`/api/meetings/${encodeURIComponent(meta.id)}/title`, { title: title.value }).then(loadState));

  const more = h('button', { class: 'icon-btn', title: 'More', 'data-menu': '1' }, icon('more'));
  more.addEventListener('click', (e) => {
    e.stopPropagation();
    openMenu(more, [
      ['redo', 'Re-process (transcribe & write notes again)', () => reprocess(meta.id), busy || meta.status === 'recording'],
      ['record', otherRecording(meta.id) ? `Stop “${otherRecording(meta.id).title}” and resume recording here` : 'Resume recording',
        () => recordOnto(meta.id, { confirmed: true }), !detail.can_record || resuming === meta.id || state.recording?.id === meta.id],
      ['copy', 'Copy notes as Markdown', () => navigator.clipboard.writeText((detail.notes_md || '').split('\n## Transcript\n')[0]).then(() => toast('Notes copied')), !detail.notes_md],
      ['download', 'Download transcript', () => downloadTranscript(meta.id), !(detail.transcript || []).length || meta.status === 'recording'],
      ['folder', 'Show in Finder', () => api(`/api/meetings/${encodeURIComponent(meta.id)}/reveal`, {})],
      ['trash', 'Delete', () => deleteCall(state.meetings.find((m) => m.id === meta.id) || meta), busy || meta.status === 'recording', true],
    ]);
  });

  const conf = meta.event && meta.event.conference_url ? conferenceName(meta.event.conference_url) : '';
  const children = [
    h('div', { class: 'note-top' },
      h('button', { class: 'crumb', onclick: () => go({ kind: view.from || 'home', pinned: true }) }, icon('back'), back),
      h('div', { class: 'note-actions' },
        h('button', { class: 'secondary', disabled: !summary, onclick: () => saveToNotes(meta.id) }, icon('share'),
          meta.apple_note_id ? 'Update Apple Note' : 'Save to Apple Notes'),
        more)),
    title,
    h('div', { class: 'chips' },
      meta.started_at ? chip('calendar', `${dayWord(meta.started_at)} · ${fmtTime(meta.started_at)}`) : null,
      meta.duration ? chip('clock', fmtLength(meta.duration)) : null,
      people.length ? chip('people', shortNames(people, 3)) : null,
      conf ? chip('video', conf) : null),
  ];
  if (!busy && (detail.speakers || []).length) children.push(renderSpeakers(meta, people));

  if (job) children.push(h('div', { class: 'status-line' }, h('span', { class: 'spinner' }), job.message || 'Working…'));
  else if (meta.status === 'error') children.push(h('div', { class: 'status-line error' }, h('span', { style: 'flex:1' }, `Something went wrong: ${meta.error}`),
    h('button', { class: 'secondary', onclick: () => reprocess(meta.id) }, 'Try again')));
  else if (meta.status === 'recorded') children.push(h('div', { class: 'status-line' }, 'This recording hasn’t been transcribed yet.',
    h('button', { class: 'primary', onclick: () => reprocess(meta.id) }, 'Transcribe & write notes')));

  const items = summary ? summary.action_items || [] : [];
  const asked = (detail.questions || []).length;
  const tabs = [['notes', 'Notes'], ['actions', `Action items${items.length ? ` · ${items.length}` : ''}`], ['mynotes', 'My notes'],
    ['ask', `Ask${asked ? ` · ${asked}` : ''}`]];
  if (!tabs.some(([key]) => key === view.tab)) view.tab = 'notes';
  children.push(h('div', { class: 'toggle' }, tabs.map(([key, label]) =>
    h('button', { class: view.tab === key ? 'active' : '', onclick: () => { view.tab = key; renderContent(); } }, label))));

  if (view.tab === 'notes') {
    children.push(detail.notes_html ? h('div', { class: 'doc', html: detail.notes_html })
      : h('p', { class: 'empty' }, busy ? 'Your notes appear here when Claude is done.' : 'No notes yet.'));
  } else if (view.tab === 'actions') {
    const list = renderActionItems(meta, items);
    children.push(list);
  } else if (view.tab === 'ask') {
    children.push(askTab(meta));
  } else {
    // The page re-renders when calls change; keep the editor you're typing in (and its undo) rather than a new one.
    const kept = document.querySelector(`.editor.mynotes[data-call="${CSS.escape(meta.id)}"]`);
    const area = kept || noteEditor({ value: detail.my_notes || '', placeholder: 'You didn’t write any notes during this call. Write some here.' });
    if (!kept) area.saved = area.getValue();  // what's saved, to tell whether you've changed it
    area.classList.add('mynotes');
    area.dataset.call = meta.id;
    const focused = kept && document.activeElement === kept && getSelection().rangeCount ? getSelection().getRangeAt(0) : null;
    if (focused) requestAnimationFrame(() => { kept.focus(); getSelection().removeAllRanges(); getSelection().addRange(focused); });
    children.push(area, editorHint(), h('div', { class: 'items-foot' },
      h('button', { class: 'primary', disabled: busy, onclick: async () => {
        const text = area.getValue();
        await api(`/api/meetings/${encodeURIComponent(meta.id)}/my-notes`, { text });
        area.saved = text;
        reprocess(meta.id);
      } }, 'Save & regenerate notes'),
      h('span', { class: 'hint' }, 'Fix or add to what you wrote, and Claude rewrites the notes around it.')));
  }
  root.replaceChildren(...children);
}

// -- asking about one call -------------------------------------------------------------------------
// A call's Ask tab: Claude's answers to what you ask in the bar at the bottom, from this call's notes and
// transcript only. The answers are kept with the call, and a follow-up sees the earlier ones.

let callAsk = null;  // the question being answered: { id, question, controller, status, step, text, error }
const ASK_STARTERS = ['What was decided?', 'What do I need to do next?', 'What questions are still open?', 'Who pushed back, and on what?'];

async function askCall(id, question) {
  question = question.trim();
  if (!question || (callAsk && callAsk.status === 'asking')) return;
  const history = (detail && detail.meta.id === id ? detail.questions || [] : []).map((q) => ({ q: q.q, a: q.a }));
  const mine = { id, question, controller: new AbortController(), status: 'asking', step: 'Reading the call', text: '', error: '' };
  callAsk = mine;
  renderCallAsk();
  try {
    const res = await fetch('/api/ask', {
      method: 'POST', signal: mine.controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-CallRec': '1' }, body: JSON.stringify({ question, call_id: id, history }),
    });
    await readAnswer(res, mine, renderCallAsk);
  } catch (e) {
    if (e.name === 'AbortError') return;
    mine.status = 'error';
    mine.error = e.message;
  }
  if (mine.status === 'done') {
    if (detail && detail.meta.id === id) detail.questions = [...(detail.questions || []), { q: question, a: mine.answer, html: mine.html }];
    if (callAsk === mine) callAsk = null;
    renderContent();  // the tab's count
  } else {
    renderCallAsk();
  }
  renderAskBar();
}

function askTab(meta) {
  if (!('questions' in detail)) {  // a server started before this existed would answer from every call instead
    return notice('Asking about one call needs Call Recorder to restart. It does so by itself when nothing is recording or being processed.');
  }
  const box = h('div', { id: 'call-ask', class: 'call-ask', 'data-call': meta.id },
    h('div', { class: 'call-ask-thread' }), h('div', { class: 'call-ask-foot' }));
  renderCallAsk(box);
  return box;
}

// An answer still coming in, formatted as it will be.
function streamingAnswer(text) {
  const answer = h('div', { class: 'doc ask-answer streaming', html: streamingHtml(text) });
  wireCallLinks(answer, true);
  return answer;
}

function renderCallAsk(box = $('#call-ask')) {
  renderAskBar();
  if (!box || !detail || box.dataset.call !== detail.meta.id) return;
  const id = box.dataset.call;
  const past = detail.questions || [];
  const pending = callAsk && callAsk.id === id ? callAsk : null;
  const qa = (q, answer) => h('div', { class: 'qa' }, h('div', { class: 'qa-q' }, q), answer);
  const thread = past.map((q) => {
    const answer = h('div', { class: 'doc ask-answer', html: q.html });
    wireCallLinks(answer, true);
    return qa(q.q, answer);
  });
  if (pending) {
    thread.push(qa(pending.question, pending.status === 'error' ? h('div', { class: 'ask-error' }, pending.error)
      : pending.text ? streamingAnswer(pending.text)
      : h('div', { class: 'ask-step' }, h('span', { class: 'spinner' }), pending.step)));
  }
  $('.call-ask-thread', box).replaceChildren(...(thread.length ? thread : [h('p', { class: 'call-ask-intro' },
    'Ask Claude anything about this call in the bar at the bottom (⌘F). It answers only from this call’s notes, your notes and the transcript, and links to the moments it used.')]));
  const busy = !!(pending && pending.status === 'asking');
  $('.call-ask-foot', box).replaceChildren(...[
    !past.length && !pending ? h('div', { class: 'ask-starters' }, ASK_STARTERS.map((q) =>
      h('button', { class: 'starter', onclick: () => askCall(id, q) }, q))) : null,
    past.length && !busy ? h('button', { class: 'link muted', onclick: async () => {
      try {
        await api(`/api/meetings/${encodeURIComponent(id)}/questions`, { clear: true });
        detail.questions = [];
        if (callAsk && callAsk.id === id) callAsk = null;
        renderContent();
      } catch (e) { toast(e.message, 'error'); }
    } }, 'Clear these questions') : null].filter(Boolean));
}

// The shortcuts, under each notepad.
const editorHint = () => h('div', { class: 'editor-hint' },
  '“- ” bullets · “1. ” numbers · “# ” heading · Tab / ⇧Tab indent · ⌘B bold · ⌘I italic · ⌘U underline · ⌘⇧X strike · ⌘⇧8 bullets · ⌘⇧7 numbers');

// The voices on the call. Naming one relabels the transcript and teaches Call Recorder that voice.
const SPEAKER_SOURCE = {
  voice: (sp) => `Recognized by voice from earlier calls (${Math.round(sp.score * 100)}% match)`,
  claude: (sp) => `Named by Claude from the conversation (${sp.confidence} confidence)`,
  you: () => 'Named by you',
  meet: (sp) => `Named from Google Meet, which showed ${sp.name} talking during ${Math.round(sp.meet * 100)}% of these lines`,
};

function renderSpeakers(meta, people) {
  const names = [...new Set([...people, ...(meta.attendees || []).map((a) => a.name)].filter(Boolean))];
  const play = (sp) => () => {
    if (!audio || audio.id !== meta.id) return;
    audio.el.currentTime = sp.first;
    audio.el.play();
  };
  const rename = (sp, node) => {
    const input = h('input', { class: 'speaker-input', list: 'speaker-names', value: sp.name || '', placeholder: sp.label,
      'aria-label': `Name for ${sp.label}` });
    let done = false;
    const save = async (commit) => {
      if (done) return;
      done = true;
      const name = input.value.trim();
      if (commit && name !== (sp.name || '')) {
        try {
          detail = await api(`/api/meetings/${encodeURIComponent(meta.id)}/speakers`, { id: sp.id, name });
          toast(name ? `Saved. Call Recorder will recognize ${name}’s voice on later calls.` : `${sp.label} is unnamed again.`);
        } catch (e) {
          toast(e.message, 'error');
        }
      }
      renderContent();
      fillMeetingTranscript();
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(true); else if (e.key === 'Escape') save(false); });
    input.addEventListener('blur', () => save(true));
    node.replaceWith(input);
    input.focus();
    input.select();
  };
  const voice = (sp) => {
    const why = (SPEAKER_SOURCE[sp.source] || (() => 'Not identified yet'))(sp);
    const node = h('span', { class: `speaker${sp.name ? '' : ' unknown'}`, style: `--spk: var(--spk-${speakerColor(sp.id)})` },
      h('button', { class: 'speaker-name', title: `${why}. Click to ${sp.name ? 'rename' : 'name'}.` },
        h('i', { class: 'dot' }), sp.name || sp.label, h('span', { class: 'talk' }, fmtLength(sp.seconds))),
      detail.has_audio ? h('button', { class: 'speaker-play', title: `Hear ${sp.name || sp.label}`, 'aria-label': `Play ${sp.name || sp.label}`, onclick: play(sp) }, '▶') : null);
    node.firstChild.addEventListener('click', () => rename(sp, node));
    return node;
  };
  const unnamed = detail.speakers.some((sp) => !sp.name);
  return h('div', { class: 'speakers' },
    h('datalist', { id: 'speaker-names' }, names.map((n) => h('option', { value: n }))),
    h('span', { class: 'speakers-label' }, 'Voices'),
    detail.speakers.map(voice),
    unnamed ? h('span', { class: 'hint' }, 'Click a voice to name it.') : null);
}

function renderActionItems(meta, items) {
  if (!items.length) return h('p', { class: 'empty' }, 'No action items.');
  return h('div', { class: 'items' }, items.map((item) => h('div', { class: 'item' },
    h('div', {}, h('div', { class: 'task' }, item.task), item.context ? h('div', { class: 'ctx' }, item.context) : null),
    h('div', { class: 'meta' }, h('span', { class: 'pill' }, item.owner), item.due ? h('span', {}, `Due ${item.due}`) : null))));
}

async function reprocess(id) {
  try { await api(`/api/meetings/${encodeURIComponent(id)}/process`, {}); await loadState(); openMeeting(id); }
  catch (e) { toast(e.message, 'error'); }
}

// Saved by the server into Downloads (the app window can't save downloads itself), then shown in Finder.
async function downloadTranscript(id) {
  try {
    const { name } = await api(`/api/meetings/${encodeURIComponent(id)}/transcript-file`, {});
    toast(`Transcript saved to Downloads: ${name}`);
  } catch (e) {
    toast(`Couldn’t save the transcript: ${e.message}`, 'error', e.hint);
  }
}

async function saveToNotes(id) {
  try {
    await api(`/api/meetings/${encodeURIComponent(id)}/apple-notes`, {});
    toast(`Saved to Apple Notes → ${state.settings.apple_notes.folder}`);
    openMeeting(id);
  } catch (e) { toast(e.message, 'error'); }
}

// -- ⋯ menu ---------------------------------------------------------------------------------------

// Entries are [icon, label, action, disabled, danger]. Opens under `at` (a button), or at the pointer (a right-click).
function openMenu(at, entries) {
  const menu = $('#menu');
  menu.replaceChildren(...entries.map(([ico, label, fn, disabled, danger]) =>
    h('button', { class: danger ? 'danger' : null, disabled: !!disabled, onclick: () => { closeMenu(); fn(); } }, icon(ico), label)));
  menu.hidden = false;
  const r = at instanceof Event ? { bottom: at.clientY - 6, right: at.clientX + menu.offsetWidth } : at.getBoundingClientRect();
  menu.style.top = `${Math.max(8, Math.min(r.bottom + 6, window.innerHeight - menu.offsetHeight - 8))}px`;
  menu.style.left = `${Math.max(8, Math.min(r.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8))}px`;
}
function closeMenu() { $('#menu').hidden = true; }
document.addEventListener('click', (e) => { if (!e.target.closest('#menu')) closeMenu(); });

document.addEventListener('keydown', (e) => {
  const typing = e.target.closest('input, textarea, [contenteditable]');
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openSearch(); }
  else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f' && view.kind === 'meeting' && askInput.isConnected) {
    e.preventDefault();  // ⌘F: ask about this call
    askInput.focus();
    askInput.select();
  } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z' && !e.shiftKey && undoable && !typing) {
    e.preventDefault();
    undoable.undo();
  } else if (e.metaKey && e.key === 'Backspace' && !typing && view.kind === 'meeting' && detail && !callBusy(detail.meta)) {
    e.preventDefault();  // ⌘⌫ deletes the open call, as in Finder
    deleteCall(state.meetings.find((m) => m.id === detail.meta.id) || detail.meta);
  }
  else if (e.key === 'Escape' && !$('#settings').open) {
    if (!$('#menu').hidden) closeMenu();
    else if (transcriptOpen) { transcriptOpen = false; renderDock(); }
  }
});

// ---------------------------------------------------------------------------------------------
// settings

const form = $('#settings-form');
const getPath = (obj, path) => path.split('.').reduce((o, k) => (o ? o[k] : undefined), obj);

async function openSettings() {
  const s = await api('/api/settings');
  state.settings = s;
  for (const field of form.elements) {
    if (!field.name) continue;
    if (field.name.startsWith('secret:')) {
      field.value = '';
      field.placeholder = s.secrets[field.name.slice(7)] ? '•••••••• saved in Keychain — type to replace' : 'Not set';
      continue;
    }
    const value = getPath(s, field.name);
    if (field.type === 'checkbox') field.checked = !!value;
    else {
      if (field.tagName === 'SELECT' && value && ![...field.options].some((o) => o.value === value)) field.append(h('option', { value }, value));
      field.value = value ?? '';
    }
  }
  renderCalendarList(s);
  renderClaude();
  $('#settings-msg').textContent = '';
  $('#settings').showModal();
  loadPermissions();
  loadMeet();
  loadMics(s.mic_device);
}

// Which of macOS Calendar's calendars hold meetings (e.g. a work Google calendar but not a personal one).
// Nothing ticked in the saved settings means all of them.
function renderCalendarList(s) {
  const names = (state.calendar && state.calendar.calendars) || [];
  const chosen = (s.calendar && s.calendar.calendars) || [];
  $('#cal-pick').hidden = !names.length || (s.calendar && s.calendar.source) !== 'macos';
  $('#cal-list').replaceChildren(...names.map((name) => h('label', { class: 'check' },
    h('input', { type: 'checkbox', value: name, checked: !chosen.length || chosen.includes(name) }), name)));
}

function chosenCalendars() {
  const boxes = [...$('#cal-list').querySelectorAll('input[type=checkbox]')];
  const ticked = boxes.filter((b) => b.checked).map((b) => b.value);
  return ticked.length === boxes.length ? [] : ticked;  // all ticked: keep following new calendars too
}

function renderClaude() {
  const s = state.settings, login = s.claude_login || {};
  const useKey = form.elements.claude_backend.value === 'api';
  $('#claude-login').hidden = useKey;
  $('#api-key-field').hidden = !useKey;
  const status = $('#claude-login-status');
  status.className = `pill${login.logged_in ? ' ok' : ' rec'}`;
  status.textContent = login.logged_in ? `Signed in${login.account ? ` as ${login.account}` : ''}`
    : login.found ? 'Not signed in' : 'Claude Code not found';
  $('#claude-signin').hidden = login.logged_in || !login.found;
  $('#claude-hint').textContent = useKey
    ? (s.secrets.anthropic_api_key ? 'API key saved. Notes are billed to your Anthropic API account.' : 'Get a key at console.anthropic.com.')
    : login.logged_in ? 'Notes are written with your Claude account. No API key needed.'
    : login.found ? 'Sign in once with your Claude account. A Terminal window and your browser will open.'
    : 'Claude Code comes with the Claude desktop app. Update or reinstall Claude, or use an API key.';
}

form.elements.claude_backend.addEventListener('change', renderClaude);

$('#claude-signin').addEventListener('click', async (e) => {
  e.target.disabled = true;
  try {
    await api('/api/claude/login', {});
    $('#claude-hint').textContent = 'Finish signing in in the Terminal window and your browser, then click Check again.';
    $('#claude-recheck').hidden = false;
  } catch (err) {
    $('#settings-msg').textContent = err.message;
  } finally {
    e.target.disabled = false;
  }
});

$('#claude-recheck').addEventListener('click', async () => {
  try { state.settings.claude_login = await api('/api/claude/status'); } catch { /* keep last status */ }
  $('#claude-recheck').hidden = !!state.settings.claude_login.logged_in;
  renderClaude();
});

const PERMISSIONS = [
  ['microphone', 'Microphone', 'Your side of the call', 'Privacy_Microphone'],
  ['system_audio', 'System audio', 'The other side of the call', 'Privacy_ScreenCapture'],
  ['calendar', 'Calendars', 'Upcoming calls and reminders', 'Privacy_Calendars'],
];

function renderPermissions(perms) {
  const label = { granted: 'Allowed', denied: 'Off', not_determined: 'Not asked yet', restricted: 'Blocked by your IT', write_only: 'Add-only (needs full access)', unknown: 'Unknown' };
  $('#perm-list').replaceChildren(...PERMISSIONS.map(([key, name, why, pane]) => {
    const value = perms[key] || 'unknown';
    return h('li', {},
      h('div', { class: 'what' }, name, h('span', {}, why)),
      h('div', {}, h('span', { class: `pill${value === 'granted' ? ' ok' : value === 'denied' ? ' rec' : ''}` }, label[value] || value),
        value === 'denied' ? h('a', { href: `x-apple.systempreferences:com.apple.preference.security?${pane}` }, 'Open settings') : null));
  }));
}

async function loadPermissions() {
  try { renderPermissions(await api('/api/permissions')); }
  catch (e) { $('#perm-list').replaceChildren(h('li', {}, e.message)); }
}

$('#perm-request').addEventListener('click', async (e) => {
  e.target.disabled = true;
  $('#settings-msg').textContent = 'Answer the macOS prompts that appear…';
  try {
    renderPermissions(await api('/api/permissions/request', {}));
    $('#settings-msg').textContent = '';
  } catch (err) {
    $('#settings-msg').textContent = err.message;
  } finally {
    e.target.disabled = false;
  }
});

// The Chrome extension that reads names and who's talking from Google Meet (see chrome-extension/).
let meetFolder = '';

async function loadMeet() {
  const pill = $('#meet-status');
  let m;
  try { m = await api('/api/meet'); } catch { m = {}; }
  meetFolder = m.folder || meetFolder;
  const ago = m.seconds_ago;
  const live = ago != null && ago < 30 && m.in_call;
  pill.className = `pill${live || ago != null ? ' ok' : ''}`;
  pill.textContent = live ? `Connected · ${m.people} other${m.people === 1 ? '' : 's'} on “${m.title || 'Meet'}”`
    : ago != null ? `Connected · last Meet call ${ago < 3600 ? `${Math.max(1, Math.round(ago / 60))} min` : `${Math.round(ago / 3600)} h`} ago`
    : 'Not connected yet';
  $('#meet-steps').hidden = ago != null;
}

async function copyText(text, what) {
  try {
    await navigator.clipboard.writeText(text);
    $('#settings-msg').textContent = `Copied ${what}.`;
  } catch {
    $('#settings-msg').textContent = text;
  }
}

$('#meet-steps').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-copy]');
  if (b) copyText(b.dataset.copy, b.dataset.copy);
});
$('#meet-copy-folder').addEventListener('click', () => copyText(meetFolder, 'the folder'));

async function loadMics(selected) {
  const select = form.elements.mic_device;
  try {
    const mics = await api('/api/mics');
    select.replaceChildren(h('option', { value: '' }, 'System default'),
      ...mics.map((m) => h('option', { value: m.id }, m.name + (m.default ? ' (default)' : ''))));
    select.value = selected || '';
  } catch { /* keep default */ }
}

$('#settings-save').addEventListener('click', async () => {
  const changes = {}, secrets = {};
  for (const field of form.elements) {
    if (!field.name) continue;
    if (field.name.startsWith('secret:')) {
      if (field.value.trim()) secrets[field.name.slice(7)] = field.value.trim();
      continue;
    }
    const value = field.type === 'checkbox' ? field.checked : field.type === 'number' ? Number(field.value) : field.value;
    const parts = field.name.split('.');
    let target = changes;
    while (parts.length > 1) { const k = parts.shift(); target = target[k] = target[k] || {}; }
    target[parts[0]] = value;
  }
  if (!$('#cal-pick').hidden) changes.calendar = { ...changes.calendar, calendars: chosenCalendars() };
  try {
    state.settings = await api('/api/settings', { ...changes, secrets });
    $('#settings-msg').textContent = 'Saved';
    loadTodos();
    setTimeout(() => $('#settings').close(), 400);
    loadState();
  } catch (e) {
    $('#settings-msg').textContent = e.message;
  }
});
$('#settings-btn').addEventListener('click', openSettings);

// ---------------------------------------------------------------------------------------------
// live updates

function connect() {
  const es = new EventSource('/api/stream');
  es.addEventListener('open', () => loadState().catch(() => {}));
  es.addEventListener('levels', (e) => setLevels(JSON.parse(e.data)));
  es.addEventListener('segment', (e) => addSegment(JSON.parse(e.data)));
  es.addEventListener('partial', (e) => showPartial(JSON.parse(e.data)));
  es.addEventListener('partial_done', (e) => document.querySelector(`.bubble.partial[data-track="${JSON.parse(e.data).track}"]`)?.remove());
  es.addEventListener('segment_removed', (e) => document.querySelector(`.bubble[data-id="${JSON.parse(e.data).id}"]`)?.remove());
  es.addEventListener('recording_started', async () => {
    await flushPrep();  // notes you were writing for the call that's starting go into it
    view = { kind: 'live' };
    await loadState();
  });
  for (const kind of ['recording_paused', 'recording_resumed']) {
    es.addEventListener(kind, (e) => {
      state.recording = JSON.parse(e.data);
      renderSidebar();
      renderDock();
    });
  }
  es.addEventListener('recording_stopped', async (e) => {
    const { id } = JSON.parse(e.data);
    const wasLive = view.kind === 'live';
    await loadState();
    if (wasLive) openMeeting(id);
  });
  es.addEventListener('job', (e) => {
    const d = JSON.parse(e.data);
    state.jobs[d.id] = d;
    const over = ['done', 'error', 'cancelled'].includes(d.step);  // cancelled: recording more onto it
    if (over) delete state.jobs[d.id];
    renderSidebar();
    if (view.kind === 'meeting' && view.id === d.id) {
      if (over) openMeeting(d.id);
      else { detail.job = d; renderContent(); }
    } else if (['home', 'calls'].includes(view.kind)) renderContent();
  });
  es.addEventListener('todos_changed', (e) => {
    const { key, done } = JSON.parse(e.data);
    if (!todos) return;
    for (const i of todos.items) if (i.key === key) i.done = done;
    renderSidebar();
    if (view.kind === 'todos') renderContent();
  });
  es.addEventListener('meetings_changed', async () => {
    loadTodos();  // new or re-processed notes may have new to-dos
    state.meetings = await api('/api/meetings');
    renderSidebar();
    if (view.kind === 'meeting') openMeeting(view.id);
    else if (view.kind !== 'live') renderContent();
  });
  es.addEventListener('calendar', (e) => {
    state.calendar = JSON.parse(e.data);
    if (prepSave) { const ev = findEvent(prepSave.id); if (ev) ev.my_notes = prepSave.text; }  // not saved yet
    renderSidebar();
    if (['home', 'upcoming', 'event'].includes(view.kind)) renderContent();
  });
  es.addEventListener('meeting_starting', (e) => { banner = { kind: 'starting', event: JSON.parse(e.data) }; renderBanner(); });
  es.addEventListener('prompt_cleared', () => {
    state.prompt = null;
    if (banner && banner.kind === 'starting') { banner = null; renderBanner(); }
  });
  es.addEventListener('waiting', (e) => {  // a call waiting for you to join (null: none any more)
    state.waiting = JSON.parse(e.data);
    waitingBanner();
    renderBanner();
    if (['home', 'upcoming', 'event'].includes(view.kind)) renderContent();
  });
  es.addEventListener('meeting_over', (e) => { banner = { kind: 'over', event: JSON.parse(e.data) }; renderBanner(); });
  es.addEventListener('notice', (e) => { const d = JSON.parse(e.data); toast(d.message, d.level); });
  es.addEventListener('live_error', (e) => toast(`Live transcription: ${JSON.parse(e.data).message}`, 'error'));
  es.addEventListener('page', (e) => pageSeen(JSON.parse(e.data).version));
}

// -- picking up updates -------------------------------------------------------------------------
// When Call Recorder's page changes (an update), the window reloads itself the next time you're not using it,
// and comes back where you were: same call, tab and scroll position. It waits while a reload would lose
// anything: writing not yet saved, an answer still coming, a call playing, an Undo still on offer.

let pageVersion = null;  // the version of the page's files this page was loaded with
let pageWaiting = false;  // a newer one is waiting
let lastInput = 0;  // when you last typed or clicked here
for (const kind of ['keydown', 'pointerdown', 'input']) document.addEventListener(kind, () => { lastInput = Date.now(); }, true);

function pageSeen(version) {
  if (pageVersion === null) pageVersion = version;
  else if (version !== pageVersion) pageWaiting = true;
}

function quietForReload() {
  if (document.querySelector('dialog[open]')) return false;
  if ((asking && asking.status === 'asking') || (callAsk && callAsk.status === 'asking')) return false;
  if (audio && !audio.el.paused) return false;
  if (undoable) return false;
  if (Date.now() - lastInput < 5000) return false;  // notes during a call are saved a moment after you stop typing
  const mine = $('.editor.mynotes');
  if (mine && mine.getValue() !== mine.saved) return false;  // My notes changed but not saved
  for (const field of document.querySelectorAll('#content input:not([type]), #content input[type=text], #content textarea')) {
    if (field.value !== field.defaultValue) return false;  // a question or a name typed but not sent
  }
  return !askInput.value.trim();
}

setInterval(() => {
  if (!pageWaiting || (document.visibilityState === 'visible' && document.hasFocus()) || !quietForReload()) return;
  const place = { hash: location.hash, view: view.kind === 'meeting' ? view : null, scroll: $('.main').scrollTop,
    transcript: transcriptOpen, transcriptScroll: $('#tpanel-body').scrollTop };
  try { sessionStorage.setItem('reload-place', JSON.stringify(place)); } catch {}
  location.reload();
}, 5000);

// After reloading for an update, put things back as they were.
async function restorePlace() {
  let place = null;
  try {
    place = JSON.parse(sessionStorage.getItem('reload-place'));
    sessionStorage.removeItem('reload-place');
  } catch {}
  if (!place || place.hash !== location.hash) return location.hash ? applyHash() : undefined;
  if (place.view) view = place.view;  // so the call opens on the same tab, with the same way back
  if (location.hash) await applyHash();
  if (view.kind === 'meeting' && detail && place.transcript) { transcriptOpen = true; renderDock(); }
  $('.main').scrollTop = place.scroll;
  $('#tpanel-body').scrollTop = place.transcriptScroll;
}

// Re-render the calendar views every minute so "now" / "past" stay current.
setInterval(() => { if (['home', 'upcoming', 'event'].includes(view.kind)) renderContent(); renderSidebar(); }, 60e3);

// ?snapshot skips the live connection, so headless browsers can tell when the page has finished loading.
const snapshot = new URLSearchParams(location.search).has('snapshot');
loadState().then(async () => { await restorePlace(); if (!snapshot) connect(); loadTodos(); }).catch((e) => toast(e.message, 'error'));
