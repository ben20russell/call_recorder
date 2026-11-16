// Passes the Meet page's reports on to the Call Recorder app on this Mac. (The page itself can't reach
// 127.0.0.1; the extension can.) If Call Recorder isn't running, reports are simply dropped.
const APP = 'http://127.0.0.1:8765';

chrome.runtime.onMessage.addListener((report, sender, reply) => {
  fetch(`${APP}/api/meet`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CallRec': '1' },
    body: JSON.stringify(report),
  }).then((r) => (r.ok ? r.json() : null)).then(reply, () => reply(null));
  return true;  // reply comes later
});
