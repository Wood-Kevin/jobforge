const { invoke } = window.__TAURI__.core;
const out = document.getElementById("out");

async function refresh() {
  try {
    out.textContent = JSON.stringify(await invoke("agent_call", { method: "GET", path: "/v1/health" }), null, 2);
  } catch (e) {
    out.textContent = "agent unavailable: " + e;
  }
}
document.getElementById("pause").onclick = () => invoke("agent_call", { method: "POST", path: "/v1/pause" }).then(refresh);
document.getElementById("resume").onclick = () => invoke("agent_call", { method: "POST", path: "/v1/resume" }).then(refresh);
refresh();
setInterval(refresh, 2000);
