#!/usr/bin/env node
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { run } = require("./checkout");
const { authenticate, createOperator, readOperator } = require("./lib/auth");
const { ordersFromText, publicOrder } = require("./lib/orders");

const root = __dirname;
const dataDir = path.resolve(process.env.TOYC_DATA_DIR || path.join(root, "data"));
const ordersDir = path.join(dataDir, "orders");
const publicDir = path.join(root, "public");
const port = Number(process.env.PORT || 8787);
const sessions = new Map();
const shots = new Set([
  "01-product.png",
  "02-cart.png",
  "03-customer.png",
  "04-payment.png",
  "05-terms.png",
  "04-after-submit.png",
  "error.png",
]);

const runState = {
  status: "idle",
  orderId: null,
  log: [],
  result: null,
  runDir: null,
  startedAt: null,
  finishedAt: null,
  controller: null,
};

function sendAttachment(res, body, filename, type) {
  res.writeHead(200, {
    "content-type": type,
    "content-disposition": `attachment; filename="${filename}"`,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  res.end(body);
}

function send(res, status, body, type = "application/json; charset=utf-8") {
  const payload = typeof body === "string" ? body : JSON.stringify(body);
  res.writeHead(status, {
    "content-type": type,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  res.end(payload);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > 1_000_000) {
        reject(new Error("Upload is larger than 1 MB"));
        req.destroy();
      } else chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function cookies(req) {
  const header = req.headers.cookie || "";
  return Object.fromEntries(
    header
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const index = part.indexOf("=");
        return [part.slice(0, index), decodeURIComponent(part.slice(index + 1))];
      })
  );
}

function sessionUser(req) {
  const token = cookies(req).toyc_panel;
  if (!token) return null;
  return sessions.get(token) || null;
}

function setSession(res, username) {
  const token = crypto.randomBytes(24).toString("hex");
  sessions.set(token, { username });
  res.setHeader(
    "set-cookie",
    `toyc_panel=${token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=43200`
  );
  return token;
}

function clearSession(req, res) {
  const token = cookies(req).toyc_panel;
  if (token) sessions.delete(token);
  res.setHeader("set-cookie", "toyc_panel=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0");
}

function requireUser(req, res) {
  const user = sessionUser(req);
  if (!user) {
    send(res, 401, { error: "Sign in required" });
    return null;
  }
  return user;
}

function listOrders() {
  fs.mkdirSync(ordersDir, { recursive: true });
  return fs
    .readdirSync(ordersDir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => JSON.parse(fs.readFileSync(path.join(ordersDir, name), "utf8")))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function note(message) {
  runState.log.push({ at: new Date().toISOString(), message });
}

async function handleApi(req, res, url) {
  if (req.method === "GET" && url.pathname === "/api/session") {
    const user = sessionUser(req);
    send(res, 200, {
      authenticated: Boolean(user),
      username: user?.username || "",
      needsSetup: !readOperator(dataDir),
    });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/setup") {
    if (readOperator(dataDir)) {
      send(res, 409, { error: "An operator account already exists" });
      return;
    }
    const body = JSON.parse((await readBody(req)).toString("utf8") || "{}");
    const operator = createOperator(dataDir, body.username, body.password);
    setSession(res, operator.username);
    send(res, 201, { username: operator.username });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/login") {
    const body = JSON.parse((await readBody(req)).toString("utf8") || "{}");
    const operator = authenticate(dataDir, body.username, body.password);
    if (!operator) {
      send(res, 401, { error: "Username or password is incorrect" });
      return;
    }
    setSession(res, operator.username);
    send(res, 200, { username: operator.username });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/logout") {
    clearSession(req, res);
    send(res, 200, { ok: true });
    return;
  }

  const user = requireUser(req, res);
  if (!user) return;

  if (req.method === "GET" && url.pathname === "/api/orders") {
    send(res, 200, { orders: listOrders().map(publicOrder) });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/orders") {
    const filename = decodeURIComponent(url.searchParams.get("name") || "");
    const text = (await readBody(req)).toString("utf8");
    const imported = ordersFromText(text, filename);
    fs.mkdirSync(ordersDir, { recursive: true });
    const saved = imported.map((config) => {
      const record = {
        id: crypto.randomBytes(8).toString("hex"),
        createdAt: new Date().toISOString(),
        sourceName: filename || "pasted order",
        config,
      };
      fs.writeFileSync(path.join(ordersDir, `${record.id}.json`), JSON.stringify(record, null, 2));
      return publicOrder(record);
    });
    send(res, 201, { orders: saved });
    return;
  }

  const orderMatch = url.pathname.match(/^\/api\/orders\/([a-f0-9]{16})$/);
  if (req.method === "DELETE" && orderMatch) {
    const file = path.join(ordersDir, `${orderMatch[1]}.json`);
    if (!file.startsWith(ordersDir)) {
      send(res, 400, { error: "Invalid order" });
      return;
    }
    if (runState.status === "running" && runState.orderId === orderMatch[1]) {
      send(res, 409, { error: "Stop the running checkout before deleting this order" });
      return;
    }
    if (fs.existsSync(file)) fs.unlinkSync(file);
    send(res, 200, { ok: true });
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/run") {
    send(res, 200, publicRun());
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/run") {
    if (runState.status === "running") {
      send(res, 409, { error: "A checkout is already running" });
      return;
    }
    const body = JSON.parse((await readBody(req)).toString("utf8") || "{}");
    const record = listOrders().find((order) => order.id === body.orderId);
    if (!record) {
      send(res, 404, { error: "Import an order before starting" });
      return;
    }
    const controller = new AbortController();
    const runDir = path.join(dataDir, "runs", `${Date.now()}-${record.id}`);
    fs.mkdirSync(runDir, { recursive: true });
    runState.status = "running";
    runState.orderId = record.id;
    runState.log = [];
    runState.result = null;
    runState.runDir = runDir;
    runState.startedAt = new Date().toISOString();
    runState.finishedAt = null;
    runState.controller = controller;
    note(`Started ${record.config.customer.name}`);
    send(res, 202, publicRun());
    run(record.config, runDir, {
      signal: controller.signal,
      onStatus: note,
    })
      .then((result) => {
        if (controller.signal.aborted || result.stopped) {
          runState.status = "stopped";
          note("Checkout stopped");
        } else {
          runState.status = "completed";
          note(result.stoppedBeforeSubmit ? "Stopped before payment" : "Order submitted");
        }
        runState.result = sanitizeResult(result);
      })
      .catch((error) => {
        runState.status = controller.signal.aborted ? "stopped" : "failed";
        runState.result = sanitizeResult(error.result || { error: error.message });
        if (!controller.signal.aborted) note(`Failed: ${error.message}`);
      })
      .finally(() => {
        runState.finishedAt = new Date().toISOString();
        runState.controller = null;
      });
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/run/stop") {
    if (runState.status === "running" && runState.controller) {
      note("Stop requested");
      runState.controller.abort();
    }
    send(res, 200, publicRun());
    return;
  }

  const orderFileMatch = url.pathname.match(/^\/api\/orders\/([a-f0-9]{16})\/order\.json$/);
  if (req.method === "GET" && orderFileMatch) {
    const record = listOrders().find((order) => order.id === orderFileMatch[1]);
    if (!record) {
      send(res, 404, { error: "Order not found" });
      return;
    }
    sendAttachment(
      res,
      JSON.stringify(record.config, null, 2),
      `order-${record.id}.json`,
      "application/json; charset=utf-8"
    );
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/run/log.txt") {
    const text = runState.log.map((entry) => `${entry.at} ${entry.message}`).join("\n");
    sendAttachment(res, text ? `${text}\n` : "", "run-log.txt", "text/plain; charset=utf-8");
    return;
  }

  const shotMatch = url.pathname.match(/^\/api\/run\/screen\/([a-z0-9-]+\.png)$/);
  if (req.method === "GET" && shotMatch && shots.has(shotMatch[1]) && runState.runDir) {
    const file = path.join(runState.runDir, shotMatch[1]);
    if (!file.startsWith(runState.runDir) || !fs.existsSync(file)) {
      send(res, 404, { error: "Screenshot not ready" });
      return;
    }
    const headers = { "content-type": "image/png", "cache-control": "no-store" };
    if (url.searchParams.get("download") === "1") {
      headers["content-disposition"] = `attachment; filename="${shotMatch[1]}"`;
    }
    res.writeHead(200, headers);
    fs.createReadStream(file).pipe(res);
    return;
  }

  send(res, 404, { error: "Not found" });
}

function publicRun() {
  const screens =
    runState.runDir && fs.existsSync(runState.runDir)
      ? fs.readdirSync(runState.runDir).filter((name) => shots.has(name))
      : [];
  return {
    status: runState.status,
    orderId: runState.orderId,
    log: runState.log,
    result: runState.result,
    screens,
    startedAt: runState.startedAt,
    finishedAt: runState.finishedAt,
  };
}

function sanitizeResult(result) {
  if (!result) return null;
  const copy = { ...result };
  delete copy.screenshots;
  return copy;
}

function serveStatic(req, res, url) {
  const requested = url.pathname === "/" ? "/index.html" : url.pathname;
  const file = path.normalize(path.join(publicDir, requested));
  if (!file.startsWith(publicDir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    send(res, 404, "Not found", "text/plain; charset=utf-8");
    return;
  }
  const types = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
  };
  res.writeHead(200, { "content-type": types[path.extname(file)] || "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  try {
    if (url.pathname.startsWith("/api/")) await handleApi(req, res, url);
    else serveStatic(req, res, url);
  } catch (error) {
    send(res, 400, { error: error.message || "Bad request" });
  }
});

if (require.main === module) {
  server.listen(port, "127.0.0.1", () => {
    console.log(`TOYC checkout panel http://127.0.0.1:${port}`);
  });
}

module.exports = { server, dataDir };
