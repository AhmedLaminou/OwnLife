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
  const noise = s.today && s.today.noise;
  document.getElementById("noise-card").hidden = !noise;
  if (noise) {
    const budget = noise.budget_seconds || 0;
    const over = noise.seconds >= budget;
    const near = !over && noise.seconds >= budget - (noise.warn_seconds || 0);
    const big = document.getElementById("noise");
    big.textContent = hm(noise.seconds);
    big.className = `big ${over ? "over" : near ? "near" : ""}`;
    document.getElementById("noise-budget").textContent = over
      ? `${hm(noise.seconds - budget)} over your ${hm(budget)}`
      : `of ${hm(budget)} · ${hm(budget - noise.seconds)} left`;
    const bar = document.getElementById("noise-bar");
    bar.style.width = `${budget ? Math.min(100, (100 * noise.seconds) / budget) : 100}%`;
    bar.className = over ? "over" : near ? "near" : "";
    document.getElementById("noise-cats").replaceChildren(...noise.categories.slice(0, 4).map((c) => row(c.name, hm(c.seconds))));
  }
  const b = s.today && s.today.blocking;
  document.getElementById("blocking").textContent = !b || !b.enabled
    ? ""
    : b.active
      ? `Noise videos blocked until midnight (limit ${hm(b.limit_seconds)}).`
      : `Noise videos blocked from ${hm(b.limit_seconds)} — ${hm(Math.max(0, b.limit_seconds - b.noise_seconds))} left.`;
  const list = document.getElementById("channels");
  list.replaceChildren(...((s.today && s.today.channels) || []).map((c) => row(c.channel, hm(c.seconds))));
  document.getElementById("open").onclick = () =>
    chrome.tabs.create({ url: s.appUrl || s.server || "http://127.0.0.1:8000" });
}

document.getElementById("options").onclick = () => chrome.runtime.openOptionsPage();
chrome.runtime.sendMessage({ type: "flush" }, () => render());
render();
