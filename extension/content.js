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
})();
