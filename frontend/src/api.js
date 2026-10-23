export async function api(path, body, headers = {}) {
  let response;
  try {
    response = await fetch(`/api${path}`, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
  } catch { throw new Error("We couldn’t connect to the service. Check that the backend is running, then try again."); }
  let data;
  try { data = await response.json(); } catch { throw new Error("The service is unavailable. Please try again."); }
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : data.error;
    throw new Error(detail ? String(detail).slice(0, 500) : "The request could not be completed. Check your inputs and try again.");
  }
  return data;
}
