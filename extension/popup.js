const STATUS = {
  ok: ["ok", "Connected to OwnLife"],
  "no-key": ["warn", "No key yet: open Settings"],
  "bad-key": ["bad", "OwnLife refused the key: create a new one"],
  offline: ["warn", "OwnLife is not running — minutes are kept and sent later"],
  error: ["bad", "OwnLife answered with an error"],
};

function hm(seconds) {
  const m = Math.round((seconds || 0) / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}` : `${m} min`;
}

function row(label, value) {
  const div = document.createElement("div");
  div.className = "row";
  const a = document.createElement("span");
  a.textContent = label;
  const b = document.createElement("b");
  b.textContent = value;
  div.append(a, b);
  return div;
}

async function render() {
  const s = await chrome.storage.local.get(["status", "today", "queue", "lastError", "server", "appUrl"]);
  const [tone, label] = STATUS[s.status] || ["", "Waiting for a first video"];
  document.getElementById("dot").className = `dot ${tone}`;
  const waiting = (s.queue || []).length;
  document.getElementById("status").textContent = waiting && s.status !== "ok" ? `${label} (${waiting} waiting)` : label;
  document.getElementById("status").title = s.lastError || "";
  document.getElementById("today").textContent = s.today ? hm(s.today.seconds) : "—";
  const list = document.getElementById("channels");
  list.replaceChildren(...((s.today && s.today.channels) || []).map((c) => row(c.channel, hm(c.seconds))));
  document.getElementById("open").onclick = () =>
    chrome.tabs.create({ url: s.appUrl || s.server || "http://127.0.0.1:8000" });
}

document.getElementById("options").onclick = () => chrome.runtime.openOptionsPage();
chrome.runtime.sendMessage({ type: "flush" }, () => render());
render();
