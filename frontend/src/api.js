const apiOrigin = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

export async function api(path, body, headers = {}, { signal, timeoutMs } = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  const timer = timeoutMs ? setTimeout(() => { timedOut = true; abort(); }, timeoutMs) : null;
  try {
    let response;
    try {
      response = await fetch(`${apiOrigin}/api${path}`, { signal: controller.signal, ...(body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) }) });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new Error(timedOut ? "The service took too long to respond. Please try again." : "We couldn’t connect to the service. Check that the backend is running, then try again.");
    }
    let data;
    try { data = await response.json(); } catch (error) {
      if (signal?.aborted) throw error;
      throw new Error(timedOut ? "The service took too long to respond. Please try again." : "The service is unavailable. Please try again.");
    }
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : data.error;
      const error = new Error(detail ? String(detail).slice(0, 500) : "The request could not be completed. Check your inputs and try again.");
      error.status = response.status;
      error.code = data.code;
      throw error;
    }
    return data;
  } finally {
    if (timer) clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}
