import { useEffect, useState } from "react";

export default function App() {
  const [state, setState] = useState({ loading: true });

  const check = async () => {
    setState({ loading: true });
    try {
      const res = await fetch("/api/snowflake/health");
      setState({ loading: false, data: await res.json() });
    } catch (e) {
      setState({ loading: false, data: { status: "error", error: `API unreachable: ${e.message}` } });
    }
  };

  useEffect(() => {
    check();
  }, []);

  const { loading, data } = state;
  const ok = data?.status === "ok";

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 640, margin: "48px auto", padding: "0 16px" }}>
      <h1>Actionable Smart Supply</h1>
      <h2>
        Snowflake connection:{" "}
        <span style={{ color: loading ? "gray" : ok ? "green" : "crimson" }}>
          {loading ? "checking…" : ok ? "connected" : "failed"}
        </span>
      </h2>
      {data && !ok && <pre style={{ whiteSpace: "pre-wrap", color: "crimson" }}>{data.error}</pre>}
      {ok && (
        <table>
          <tbody>
            {Object.entries(data)
              .filter(([k]) => k !== "status")
              .map(([k, v]) => (
                <tr key={k}>
                  <th style={{ textAlign: "left", paddingRight: 16 }}>{k}</th>
                  <td>{v ?? "—"}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
      <button onClick={check} disabled={loading} style={{ marginTop: 16 }}>
        Re-check
      </button>
    </main>
  );
}
