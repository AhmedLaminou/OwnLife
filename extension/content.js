// Runs on YouTube pages. While the main video plays, it sends a heartbeat every
// 15 seconds ("this video was playing now"), and one when the video starts,
// pauses or ends. Paused time, the home page's silent previews and closed tabs
// count for nothing: only playing time is measured.
(() => {
  const BEAT_MS = 15000;

  function videoIdOf(href) {
    try {
      const u = new URL(href);
      if (u.pathname === "/watch") return u.searchParams.get("v");
      const m = u.pathname.match(/^\/(shorts|live|embed)\/([A-Za-z0-9_-]{6,20})/);
      return m ? m[2] : null;
    } catch {
      return null;
    }
  }

  function text(selector) {
    const el = document.querySelector(selector);
    const t = el && el.textContent ? el.textContent.trim() : "";
    return t || null;
  }

  function mainVideo() {
    return (
      document.querySelector("ytd-reel-video-renderer[is-active] video") ||
      document.querySelector("#movie_player video.html5-main-video") ||
      document.querySelector("#movie_player video") ||
      document.querySelector("#player-container-id video") || // m.youtube.com
      null
    );
  }

  function isPlaying() {
    const v = mainVideo();
    return !!v && !v.paused && !v.ended && v.readyState > 2;
  }

  function details() {
    const video_id = videoIdOf(location.href);
    if (!video_id) return null;
    const shorts = location.pathname.startsWith("/shorts/");
    const title =
      (shorts
        ? text("ytd-reel-video-renderer[is-active] h2") || text("ytd-reel-video-renderer[is-active] .title")
        : text("ytd-watch-metadata h1") || text("#title h1")) ||
      document.title.replace(/^\(\d+\)\s*/, "").replace(/ - YouTube$/, "").trim();
    const channel = shorts
      ? text("ytd-reel-video-renderer[is-active] ytd-channel-name a") ||
        text("ytd-reel-video-renderer[is-active] #channel-name a")
      : text("ytd-watch-metadata ytd-channel-name a") ||
        text("#owner ytd-channel-name a") ||
        text("ytd-video-owner-renderer ytd-channel-name a");
    // When the channel is not found in the page, OwnLife looks it up itself.
    return { video_id, title, channel, url: location.href };
  }

  function beat() {
    const d = details();
    if (!d) return;
    try {
      chrome.runtime.sendMessage({ type: "heartbeat", hb: { ...d, ts: new Date().toISOString() } });
    } catch {
      // The extension was updated or reloaded: this tab's script is orphaned until the page is refreshed.
    }
  }

  setInterval(() => {
    if (isPlaying()) beat();
  }, BEAT_MS);

  // Media events do not bubble, but they can be caught on their way down (capture phase).
  for (const type of ["playing", "pause", "ended"]) {
    document.addEventListener(
      type,
      (e) => {
        if (e.target === mainVideo()) beat();
      },
      true,
    );
  }
  window.addEventListener("pagehide", () => {
    if (isPlaying()) beat();
  });

  // ---------------------------------------------------------------- blocking noise
  // Before a video plays, and every minute while it is open, OwnLife is asked
  // whether it may play (its category, today's noise against your limit, the
  // channels and categories you block). A blocked video is paused behind a
  // screen. When OwnLife does not answer (not running), nothing is blocked.
  const RECHECK_MS = 60000;
  let verdict = null;
  let checkedId = null;
  let checkedHref = null;
  let host = null;
  let mutedByUs = false;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function hm(seconds) {
    const m = Math.round((seconds || 0) / 60);
    return m >= 60 ? `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}` : `${m} min`;
  }

  function hold() {
    const v = mainVideo();
    if (v && !v.paused) v.pause();
    if (v && !v.muted) {
      v.muted = true;
      mutedByUs = true;
    }
  }

  function texts(r) {
    const cat = (r.category && r.category.name) || "noise";
    if (r.reason === "channel") {
      return [`${r.channel} is blocked`, "You chose to block this channel, every day."];
    }
    if (r.reason === "category") {
      return [`${cat} is blocked`, `You chose to block ${cat}, every day.`];
    }
    return [
      `Your ${hm(r.limit_seconds)} of noise are spent for today`,
      `This video is in ${cat}. Noise is blocked until midnight — ${hm(r.noise_seconds)} of noise today.`,
    ];
  }

  function block(r) {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    hold();
    if (!host) {
      host = document.createElement("div");
      host.style.cssText = "position:fixed;inset:0;z-index:2147483647;";
      const root = host.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>
          .wrap { position: fixed; inset: 0; display: grid; place-items: center; background: rgba(6,10,17,.97);
                  font: 15px/1.5 system-ui, "Segoe UI", sans-serif; color: #e6edf6; }
          .card { max-width: 520px; padding: 32px; border: 1px solid rgba(148,163,184,.18); border-radius: 16px; background: #0f1724; text-align: center; }
          .brand { font-weight: 600; color: #22d3ee; letter-spacing: .02em; margin-bottom: 14px; }
          h1 { font-size: 22px; margin: 0 0 10px; }
          p { color: #b6c2d2; margin: 0 0 22px; }
          button { height: 36px; padding: 0 16px; border-radius: 10px; border: 0; background: #22d3ee; color: #032029; font: inherit; font-weight: 600; cursor: pointer; }
          .small { margin: 18px 0 0; font-size: 12px; color: #7d8aa0; }
        </style>
        <div class="wrap"><div class="card">
          <div class="brand">OwnLife</div><h1></h1><p></p>
          <button type="button">Go back</button>
          <div class="small">Change it in OwnLife → Watching → Blocking noise.</div>
        </div></div>`;
      root.querySelector("button").onclick = () => (history.length > 1 ? history.back() : location.assign("https://www.youtube.com/"));
      document.documentElement.appendChild(host);
    }
    const [title, body] = texts(r);
    const root = host.shadowRoot;
    root.querySelector("h1").textContent = title;
    root.querySelector("p").textContent = body;
  }

  function unblock() {
    if (host) {
      host.remove();
      host = null;
    }
    const v = mainVideo();
    if (mutedByUs && v) v.muted = false;
    mutedByUs = false;
  }

  async function check(force) {
    let d = details();
    if (!d) {
      verdict = null;
      checkedId = null;
      unblock();
      return;
    }
    if (!force && d.video_id === checkedId) return;
    if (d.video_id !== checkedId) {
      verdict = null; // another video: the last answer was about the previous one
      unblock();
    }
    checkedId = d.video_id;
    for (let i = 0; i < 6 && !d.channel; i++) {
      // the channel's name appears a moment after the page changes
      await sleep(500);
      d = details() || d;
    }
    try {
      chrome.runtime.sendMessage({ type: "check", video: d }, (r) => {
        if (chrome.runtime.lastError || !r) return; // OwnLife not running: nothing is blocked
        const now = details();
        if (!now || now.video_id !== d.video_id) return; // the page moved on meanwhile
        verdict = r;
        if (r.blocked) block(r);
        else unblock();
      });
    } catch {
      // The extension was reloaded: this tab's script no longer reaches it.
    }
  }

  // A blocked video stays paused, whatever starts it (autoplay, a key, a click).
  document.addEventListener(
    "play",
    (e) => {
      if (verdict && verdict.blocked && e.target === mainVideo()) hold();
    },
    true,
  );
  document.addEventListener("yt-navigate-finish", () => check(false));
  setInterval(() => {
    if (location.href !== checkedHref) {
      checkedHref = location.href;
      check(false);
    }
  }, 1000);
  setInterval(() => check(true), RECHECK_MS);
})();
