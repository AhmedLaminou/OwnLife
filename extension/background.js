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

chrome.alarms.create("flush", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "flush") flush();
});

chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") chrome.runtime.openOptionsPage();
});
