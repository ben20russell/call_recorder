// Call Recorder for Google Meet: whether this tab has its microphone on. Meet keeps the microphone on for as
// long as you're on a call (muted too) and turns it off when you leave, so this tells Call Recorder you left a
// call even when you go straight on to another Meet call and Chrome as a whole never lets go of the microphone.
// It runs inside the page, where Meet asks for the microphone, and shares only how many of the page's
// microphone tracks are on, as an attribute on the page's root element that meet.js reads. Until the page has
// asked for the microphone the attribute isn't there at all ("don't know", not "off").
(() => {
  const tracks = new Set();     // WeakRefs, so a track Meet drops can still be cleaned up by the browser
  const seen = new WeakSet();
  let shown = null;

  function show() {
    let live = 0;
    for (const ref of tracks) {
      const track = ref.deref();
      if (!track) tracks.delete(ref);
      else if (track.readyState === 'live') live += 1;
    }
    if (String(live) !== shown && document.documentElement) {
      shown = String(live);
      document.documentElement.setAttribute('data-callrec-mic', shown);
    }
  }

  function watch(track) {
    if (track.kind !== 'audio' || seen.has(track)) return;
    seen.add(track);
    tracks.add(new WeakRef(track));
    track.addEventListener('ended', show);  // unplugged
    show();
  }

  const { getUserMedia } = MediaDevices.prototype;
  MediaDevices.prototype.getUserMedia = function (...args) {
    return getUserMedia.apply(this, args).then((stream) => {
      stream.getAudioTracks().forEach(watch);
      return stream;
    });
  };
  const { clone, stop } = MediaStreamTrack.prototype;
  MediaStreamTrack.prototype.clone = function () {
    const copy = clone.apply(this);
    if (seen.has(this)) watch(copy);
    return copy;
  };
  MediaStreamTrack.prototype.stop = function () {
    stop.apply(this);
    if (seen.has(this)) show();
  };
  setInterval(() => tracks.size && show(), 1000);  // tracks Meet let go of without stopping them
})();
