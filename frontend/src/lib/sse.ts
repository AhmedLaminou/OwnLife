// Reads the assistant's Server-Sent Events from a POST request. EventSource only
// does GET, so the stream is parsed by hand from fetch's ReadableStream.

export interface StreamHandlers {
  onEvent: (event: string, data: Record<string, unknown>) => void;
  signal?: AbortSignal;
}

export async function postStream(path: string, body: unknown, { onEvent, signal }: StreamHandlers): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", "X-OwnLife": "1" },
    body: JSON.stringify(body),
    credentials: "same-origin",
    signal,
  });
  if (!res.ok || !res.body) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (typeof j?.detail === "string") message = j.detail;
    } catch {
      /* keep the status line */
    }
    onEvent("error", { message });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data.push(line.slice(6));
      }
      if (!data.length) continue;
      try {
        onEvent(event, JSON.parse(data.join("\n")));
      } catch {
        onEvent(event, { raw: data.join("\n") });
      }
    }
  }
}
