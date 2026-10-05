// Collects heartbeats from YouTube tabs and sends them to OwnLife, on this
// computer, in batches. While OwnLife is not running they wait here (about three
// days of watching fit) and are sent when it is back.

const DEFAULTS = { server: "http://127.0.0.1:8000", token: "" };
const MAX_QUEUE = 20000;
const BATCH = 500;
const VERSION = chrome.runtime.getManifest().version;

// chrome.storage has no transactions: changes to the queue go through one chain.
let chain = Promise.resolve();
function locked(fn) {
  const p = chain.then(fn, fn);
  chain = p.catch(() => {});
  return p;
}

async function config() {
  const s = await chrome.storage.local.get(["server", "token"]);
  return { server: (s.server || DEFAULTS.server).replace(/\/+$/, ""), token: (s.token || "").trim() };
}

function enqueue(hb) {
  return locked(async () => {
    const { queue = [] } = await chrome.storage.local.get("queue");
    queue.push(hb);
    if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);
    await chrome.storage.local.set({ queue });
  });
}

let flushing = false;

async function flush() {
  if (flushing) return;
  flushing = true;
  try {
    const { server, token } = await config();
    if (!token) {
      await chrome.storage.local.set({ status: "no-key" });
      return;
    }
    for (;;) {
      const { queue = [] } = await chrome.storage.local.get("queue");
      if (!queue.length) break;
      const batch = queue.slice(0, BATCH);
      const r = await fetch(`${server}/api/ingest/youtube`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, "X-OwnLife": "1" },
        body: JSON.stringify({ heartbeats: batch, client: `ownlife-extension/${VERSION}` }),
      });
      if (!r.ok) {
        const detail = (await r.text()).slice(0, 200);
        await chrome.storage.local.set({ status: r.status === 401 ? "bad-key" : "error", lastError: `${r.status} ${detail}` });
        return;
      }
      const data = await r.json();
      await locked(async () => {
        const { queue: current = [] } = await chrome.storage.local.get("queue");
        await chrome.storage.local.set({ queue: current.slice(batch.length) });
      });
      await chrome.storage.local.set({ status: "ok", lastError: null, lastSync: new Date().toISOString(), today: data.today });
    }
  } catch (e) {
    await chrome.storage.local.set({ status: "offline", lastError: String(e) });
  } finally {
    flushing = false;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === "heartbeat" && msg.hb) {
    enqueue(msg.hb).then(flush);
    return false;
  }
  if (msg && msg.type === "flush") {
    flush().then(() => reply({ ok: true }));
    return true; // the reply comes later
  }
  return false;
});

// When nothing was sent for a minute, ask OwnLife for today's numbers anyway:
// the noise of the day also grows outside YouTube (the window tracker, your entries).
async function refresh() {
  const { server, token } = await config();
  if (!token) return;
  const { lastSync } = await chrome.storage.local.get("lastSync");
  if (lastSync && Date.now() - Date.parse(lastSync) < 50000) return;
  try {
    const r = await fetch(`${server}/api/ingest/ping`, { headers: { Authorization: `Bearer ${token}`, "X-OwnLife": "1" } });
    if (!r.ok) return;
    const data = await r.json();
    await chrome.storage.local.set({ status: "ok", lastError: null, lastSync: new Date().toISOString(), today: data.today });
  } catch {
    // OwnLife is not running: the badge keeps its last number (until the day ends)
  }
}

function hm(m) {
  return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}`;
}

// The icon's badge: today's noise in minutes — grey, amber when the budget is
// nearly spent, red once it is.
async function badge() {
  const { today, lastSync } = await chrome.storage.local.get(["today", "lastSync"]);
  const noise = today && today.noise;
  const fresh = lastSync && new Date(lastSync).toDateString() === new Date().toDateString();
  const minutes = noise && fresh ? Math.round(noise.seconds / 60) : 0;
  if (!minutes) {
    await chrome.action.setBadgeText({ text: "" });
    await chrome.action.setTitle({ title: "OwnLife — YouTube time" });
    return;
  }
  const budget = noise.budget_seconds || 0;
  const color = noise.seconds >= budget ? "#dc2626" : noise.seconds >= budget - (noise.warn_seconds || 0) ? "#d97706" : "#475569";
  await chrome.action.setBadgeText({ text: hm(minutes) });
  await chrome.action.setBadgeBackgroundColor({ color });
  if (chrome.action.setBadgeTextColor) await chrome.action.setBadgeTextColor({ color: "#ffffff" });
  await chrome.action.setTitle({ title: `OwnLife — noise today: ${hm(minutes)} of ${hm(Math.round(budget / 60))}` });
}

chrome.storage.onChanged.addListener((changes) => {
  if (changes.today || changes.lastSync) badge();
});
badge();

chrome.alarms.create("flush", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "flush") flush().then(refresh).then(badge);
});

chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") chrome.runtime.openOptionsPage();
});
