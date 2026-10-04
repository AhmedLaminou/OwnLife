const $ = (id) => document.getElementById(id);

async function load() {
  const s = await chrome.storage.local.get(["token", "server", "appUrl"]);
  $("token").value = s.token || "";
  $("server").value = s.server || "";
  $("app").value = s.appUrl || "";
}

async function save() {
  const server = ($("server").value.trim() || "http://127.0.0.1:8000").replace(/\/+$/, "");
  const token = $("token").value.trim();
  const appUrl = $("app").value.trim().replace(/\/+$/, "");
  await chrome.storage.local.set({ server, token, appUrl });
  const out = $("result");
  out.textContent = "Testing…";
  try {
    const r = await fetch(`${server}/api/ingest/ping`, { headers: { Authorization: `Bearer ${token}` } });
    if (r.ok) {
      const data = await r.json();
      out.textContent = `Connected to ${data.name}'s OwnLife. Play a video: its minutes appear in the ledger within a minute.`;
      await chrome.storage.local.set({ status: "ok", today: data.today, lastError: null });
      chrome.runtime.sendMessage({ type: "flush" });
    } else {
      out.textContent = r.status === 401 ? "The key was refused: create a new one in OwnLife and paste it here." : `OwnLife answered ${r.status}.`;
    }
  } catch {
    out.textContent = `OwnLife does not answer at ${server}. Is it running?`;
  }
}

$("save").onclick = save;
load();
