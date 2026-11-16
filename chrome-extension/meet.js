// Call Recorder for Google Meet. While you're on a Meet call, tells the Call Recorder app on this Mac the
// names on the call's tiles and whose tile shows them talking, so its transcripts can say who said what, and
// when you leave the call (the tiles go and Meet turns the microphone off, see mic.js, or the tab closes), so
// the recording can stop. Only names, talking / not talking and microphone on / off leave the page, and only
// to Call Recorder at 127.0.0.1: no audio, video, chat or captions.
//
// Meet has no public hook for "who's talking", and its class names change every few months, so this doesn't
// look for particular classes. A talking person's tile animates a sound-level indicator, which changes several
// times a second while they talk; the rest of a tile only changes now and then (hover, camera on/off). So a
// tile whose small parts keep changing is someone talking. Call Recorder checks the result against the call's
// audio afterwards and ignores it if the two don't line up.
(() => {
  const SLICE = 100;        // ms; indicator activity is counted per slice of time
  const WINDOW = 800;       // ms of activity looked at
  const TALKING = 3;        // slices with activity within WINDOW that mean someone is talking
  const HOLD = 500;         // ms a tile stays "talking" after its indicator goes still
  const TICK = 150;         // ms between checks
  const SEND_EVERY = 500;   // ms between reports while who's talking changes
  const HEARTBEAT = 5000;   // ms between reports otherwise
  const ROSTER_EVERY = 1000;
  const MEETING = /^\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?])/;
  const ICON = /^[a-z0-9_]+$/;  // Material icon names ("mic_off", "more_vert") sit in tiles as text
  const PRESENTING = /\s*\((?:presentation|presenting)\)$/i;

  const slices = new Map();    // participant id → recent time slices with indicator activity
  const lastTalk = new Map();  // participant id → when it last counted as talking
  const names = new Map();     // participant id → name on the tile (people only, not presentations)
  const selfIds = new Set();   // your own tile; kept once seen (Meet sometimes drops the marker briefly)
  const stats = new Map();     // participant id → {slices, parts: Map(part → changes)}, for the 5-s check-in
  let current = { talking: [], me: false, seen: true };
  let events = [];
  let lastSent = 0;
  let lastRoster = 0;
  let wasInCall = false;
  let wasMic = null;
  let lastCode;
  let closing = false;

  const clean =(text) => (text || '').replace(/\s+/g, ' ').trim();

  function nameOf(tile) {
    for (const el of tile.querySelectorAll('.notranslate, [data-self-name]')) {
      const text = clean(el.textContent);
      if (text && !ICON.test(text) && text.length <= 80) return text;
    }
    return null;
  }

  function readRoster() {
    names.clear();
    for (const tile of document.querySelectorAll('[data-participant-id]')) {
      const id = tile.getAttribute('data-participant-id');
      if (!id || names.has(id)) continue;
      if (tile.matches('[data-self-name]') || tile.querySelector('[data-self-name]')) selfIds.add(id);
      const name = nameOf(tile);
      if (name && !PRESENTING.test(name)) names.set(id, name);
    }
  }

  // A short description of an element (no text), for the check-in that shows what moved on each tile.
  const part = (el) => el.tagName.toLowerCase() + (el.getAttribute('jsname') ? `[${el.getAttribute('jsname')}]` : '')
    + [...el.classList].slice(0, 3).map((c) => `.${c}`).join('');

  const observer = new MutationObserver((records) => {
    const slice = Math.floor(Date.now() / SLICE);
    for (const r of records) {
      const el = r.target;
      if (el.nodeType !== 1 || el.tagName === 'VIDEO' || el.tagName === 'CANVAS' || el.tagName === 'IMG') continue;
      if (r.type === 'attributes' && r.oldValue === el.getAttribute(r.attributeName)) continue;  // set, but not changed
      const tile = el.closest('[data-participant-id]');
      if (!tile || tile === el || el.querySelector('video, canvas')) continue;  // the tile itself, or the picture
      const id = tile.getAttribute('data-participant-id');
      let s = slices.get(id);
      if (!s) slices.set(id, (s = []));
      let st = stats.get(id);
      if (!st) stats.set(id, (st = { slices: 0, parts: new Map() }));
      if (s[s.length - 1] !== slice) {
        s.push(slice);
        st.slices += 1;
      }
      const p = part(el);
      st.parts.set(p, (st.parts.get(p) || 0) + 1);
    }
  });

  function tick() {
    const now = Date.now();
    if (now - lastRoster >= ROSTER_EVERY) {
      readRoster();
      lastRoster = now;
    }
    const oldest = Math.floor(now / SLICE) - WINDOW / SLICE;
    const talking = new Set();
    let me = false;
    for (const [id, s] of slices) {
      while (s.length && s[0] <= oldest) s.shift();
      // Starts after a few busy slices; once talking, any movement keeps it going.
      const going = now - (lastTalk.get(id) ?? -1e9) <= HOLD;
      if (s.length >= TALKING || (going && s.length)) lastTalk.set(id, (s[s.length - 1] + 1) * SLICE);
      if (now - (lastTalk.get(id) ?? -1e9) > HOLD) continue;
      if (selfIds.has(id)) me = true;
      else if (names.has(id)) talking.add(names.get(id));
    }
    const next = { talking: [...talking].sort(), me, seen: document.visibilityState === 'visible' };
    if (JSON.stringify(next) !== JSON.stringify(current)) {
      current = next;
      events.push({ t: now, ...next });
    }
    send(now);
  }

  // Whether the page has its microphone on (mic.js); null until the page has asked for it.
  function micOn() {
    const live = document.documentElement.getAttribute('data-callrec-mic');
    return live === null ? null : Number(live) > 0;
  }

  function send(now) {
    const here = (location.pathname.match(MEETING) || [])[1];
    const inCall = !closing && !!here && names.size > 0;
    const mic = closing ? false : micOn();
    if (inCall) lastCode = here;
    if (!inCall && !wasInCall && mic === wasMic) return;  // not on a call: nothing to say
    const due = events.length ? now - lastSent >= SEND_EVERY : now - lastSent >= HEARTBEAT;
    if (!due && inCall === wasInCall && mic === wasMic) return;
    const report = {
      t: now, code: here || lastCode, in_call: inCall, mic, closed: closing || undefined,
      title: clean(document.title).replace(/^(?:Google )?Meet\s*[-–—]\s*/, ''),
      self: [...selfIds].map((id) => names.get(id)).find(Boolean) || null,
      participants: [...new Set([...names].filter(([id]) => !selfIds.has(id)).map(([, n]) => n))].sort(),
      ...current, events,
    };
    if (now - lastSent >= HEARTBEAT || !inCall) {  // a check-in on what's moving on each tile, for Call Recorder's log
      report.check = Object.fromEntries([...stats].map(([id, st]) => [selfIds.has(id) ? 'You' : names.get(id) || id.slice(-6), {
        slices: st.slices, parts: [...st.parts].sort((a, b) => b[1] - a[1]).slice(0, 3) }]));
      stats.clear();
    }
    events = [];
    lastSent = now;
    wasInCall = inCall;
    wasMic = mic;
    try {
      chrome.runtime.sendMessage(report, () => void chrome.runtime.lastError);
    } catch {  // the extension was reloaded or removed: this copy of the script is orphaned
      observer.disconnect();
      clearInterval(timer);
    }
  }

  observer.observe(document.documentElement, { attributes: true, attributeOldValue: true, attributeFilter: ['class', 'style'],
    childList: true, subtree: true });
  const timer = setInterval(tick, TICK);
  document.addEventListener('visibilitychange', tick);
  window.addEventListener('pagehide', () => {  // the tab is closing or going elsewhere: you've left its call
    closing = true;
    send(Date.now());
  });
  window.addEventListener('pageshow', () => { closing = false; });  // ...unless it comes back (Back button)
})();
