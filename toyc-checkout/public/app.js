const gate = document.querySelector("#gate");
const app = document.querySelector("#app");
const gateForm = document.querySelector("#gate-form");
const gateTitle = document.querySelector("#gate-title");
const gateCopy = document.querySelector("#gate-copy");
const gateError = document.querySelector("#gate-error");
const gateSubmit = document.querySelector("#gate-submit");
const ordersEl = document.querySelector("#orders");
const summary = document.querySelector("#summary");
const orderTitle = document.querySelector("#order-title");
const toggle = document.querySelector("#toggle");
const statusEl = document.querySelector("#status");
const logEl = document.querySelector("#log");
const screen = document.querySelector("#screen");
const importError = document.querySelector("#import-error");

let needsSetup = false;
let orders = [];
let selectedId = "";
let run = { status: "idle", log: [] };

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

function showGate() {
  gate.hidden = false;
  app.hidden = true;
  gateTitle.textContent = needsSetup ? "Create operator account" : "Sign in";
  gateCopy.textContent = needsSetup
    ? "This panel keeps imported orders private. Create the account that can start checkout."
    : "Use the operator account for this panel.";
  gateSubmit.textContent = needsSetup ? "Create account" : "Sign in";
  document.querySelector("#password").autocomplete = needsSetup ? "new-password" : "current-password";
}

function showApp(username) {
  gate.hidden = true;
  app.hidden = false;
  document.querySelector("#who").textContent = username;
}

function renderOrders() {
  ordersEl.innerHTML = "";
  if (!orders.length) {
    ordersEl.innerHTML = "<li class='muted'>No orders imported yet.</li>";
    return;
  }
  for (const order of orders) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(order.id === selectedId));
    const product = order.productUrl.split("/").filter(Boolean).pop();
    button.textContent = `${order.customerName} · ${product}`;
    const detail = document.createElement("small");
    detail.textContent = `${order.accountEmail || "Guest checkout"} · qty ${order.quantity}`;
    button.append(detail);
    button.addEventListener("click", () => {
      selectedId = order.id;
      renderOrders();
      renderDesk();
    });
    item.append(button);
    ordersEl.append(item);
  }
}

function renderDesk() {
  const order = orders.find((item) => item.id === selectedId);
  const running = run.status === "running";
  summary.innerHTML = "";
  if (!order) {
    orderTitle.textContent = "No order selected";
    toggle.disabled = true;
  } else {
    orderTitle.textContent = order.customerName;
    const rows = [
      ["Product", order.productUrl],
      ["Quantity", String(order.quantity)],
      ["Delivery", [order.deliveryMethod, order.district, order.storeContains].filter(Boolean).join(" · ")],
      ["Payment", order.paymentMethod || "Default"],
      ["Shop account", order.accountEmail || "Guest, no shop login"],
      ["Email", order.customerEmail],
      ["Phone", order.customerPhone],
    ];
    for (const [label, value] of rows) {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = value;
      summary.append(term, detail);
    }
    toggle.disabled = !running && run.status === "running";
    toggle.disabled = false;
  }
  if (!order && !running) toggle.disabled = true;
  toggle.textContent = running ? "Stop" : "Start";
  toggle.className = running ? "stop" : "start";
  statusEl.textContent = run.status;
  statusEl.dataset.status = run.status;
  logEl.innerHTML = "";
  for (const entry of run.log || []) {
    const item = document.createElement("li");
    item.textContent = `${entry.at.slice(11, 19)} ${entry.message}`;
    logEl.append(item);
  }
  const available = run.screens || [];
  const shot = ["05-terms.png", "03-customer.png", "04-payment.png", "02-cart.png", "01-product.png"].find(
    (name) => available.includes(name)
  );
  if (shot) {
    screen.hidden = false;
    screen.alt = "Latest checkout screen";
    const next = `/api/run/screen/${shot}?t=${run.finishedAt || run.startedAt || ""}`;
    if (screen.dataset.shot !== next) {
      screen.dataset.shot = next;
      screen.src = next;
    }
  } else {
    screen.hidden = true;
    screen.removeAttribute("src");
    delete screen.dataset.shot;
  }
}

async function refresh() {
  const session = await api("/api/session");
  needsSetup = session.needsSetup;
  if (!session.authenticated) {
    showGate();
    return;
  }
  showApp(session.username);
  orders = (await api("/api/orders")).orders;
  if (!selectedId && orders[0]) selectedId = orders[0].id;
  run = await api("/api/run");
  renderOrders();
  renderDesk();
}

gateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  gateError.textContent = "";
  const payload = JSON.stringify({
    username: document.querySelector("#username").value,
    password: document.querySelector("#password").value,
  });
  try {
    await api(needsSetup ? "/api/setup" : "/api/login", { method: "POST", body: payload });
    gateForm.reset();
    await refresh();
  } catch (error) {
    gateError.textContent = error.message;
  }
});

document.querySelector("#logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  selectedId = "";
  await refresh();
});

document.querySelector("#file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  importError.textContent = "";
  if (!file) return;
  try {
    const saved = await api(`/api/orders?name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { "content-type": "text/plain; charset=utf-8" },
      body: await file.text(),
    });
    selectedId = saved.orders[0]?.id || selectedId;
    await refresh();
  } catch (error) {
    importError.textContent = error.message;
  }
  event.target.value = "";
});

toggle.addEventListener("click", async () => {
  toggle.disabled = true;
  importError.textContent = "";
  try {
    if (run.status === "running") await api("/api/run/stop", { method: "POST", body: "{}" });
    else await api("/api/run", { method: "POST", body: JSON.stringify({ orderId: selectedId }) });
    await refresh();
  } catch (error) {
    importError.textContent = error.message;
    toggle.disabled = false;
  }
});

setInterval(() => {
  if (!app.hidden) refresh().catch(() => {});
}, 1000);

refresh().catch((error) => {
  gate.hidden = false;
  gateError.textContent = error.message;
});
