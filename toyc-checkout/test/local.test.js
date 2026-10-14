const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { createOperator, authenticate, readOperator } = require("../lib/auth");
const { ordersFromText, publicOrder } = require("../lib/orders");
const {
  classifySubmit,
  isOrderPost,
  isTransient,
  loginWallError,
  normalizeOrder,
  withRetry,
  CheckoutError,
  CheckoutStopped,
} = require("../checkout");

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "toyc-panel-"));
}

test("operator account accepts the right password only", () => {
  const dir = tempDir();
  assert.equal(readOperator(dir), null);
  createOperator(dir, "alex", "correct-horse");
  assert.equal(authenticate(dir, "alex", "correct-horse").username, "alex");
  assert.equal(authenticate(dir, "alex", "wrong-password"), null);
  assert.throws(() => createOperator(dir, "other", "correct-horse"), /already exists/);
});

test("json and csv imports become checkout orders without exposing passwords", () => {
  const json = ordersFromText(
    JSON.stringify({
      productUrl: "https://www.toyc.com.hk/products/example",
      customer: { name: "陳大文", email: "ada@example.com", phone: "51234567" },
      account: { email: "ada@example.com", password: "shop-secret" },
    }),
    "order.json"
  );
  assert.equal(json[0].account.password, "shop-secret");
  const view = publicOrder({
    id: "a".repeat(16),
    createdAt: "2026-10-14T00:00:00.000Z",
    sourceName: "order.json",
    config: json[0],
  });
  assert.equal(view.accountEmail, "ada@example.com");
  assert.equal(JSON.stringify(view).includes("shop-secret"), false);

  const csv = ordersFromText(
    "productUrl,customerName,customerEmail,customerPhone,quantity\nhttps://www.toyc.com.hk/products/example,陳大文,ada@example.com,51234567,2\n",
    "orders.csv"
  );
  assert.equal(csv[0].quantity, 2);
  assert.equal(csv[0].account, undefined);
});

test("the sample download files import", () => {
  const json = fs.readFileSync(path.join(__dirname, "../public/sample-order.json"), "utf8");
  const csv = fs.readFileSync(path.join(__dirname, "../public/sample-orders.csv"), "utf8");
  assert.equal(ordersFromText(json, "sample-order.json")[0].customer.name, "測試 用戶");
  assert.equal(ordersFromText(csv, "sample-orders.csv")[0].quantity, 1);
  assert.equal(ordersFromText(csv, "sample-orders.csv")[0].account, undefined);
});

test("retry settings default to three fast attempts and reject a bad count", () => {
  const order = normalizeOrder({
    productUrl: "https://www.toyc.com.hk/products/example",
    customer: { name: "陳大文", email: "ada@example.com", phone: "51234567" },
  });
  assert.equal(order.retries, 3);
  assert.equal(order.retryDelayMs, 300);
  assert.throws(() => normalizeOrder({ ...order, retries: 0 }), /retries must be an integer from 1 to 5/);
});

test("a failed step is retried and a rejected order is not", async () => {
  let calls = 0;
  const value = await withRetry("Open product", async () => {
    calls += 1;
    if (calls < 3) throw new Error("Navigation timeout");
    return "ready";
  }, { retries: 3, delayMs: 0 });
  assert.equal(value, "ready");
  assert.equal(calls, 3);

  let soldOutCalls = 0;
  await assert.rejects(
    () =>
      withRetry("Open product", async () => {
        soldOutCalls += 1;
        throw new CheckoutError("item is not available (售完)", { retryable: false });
      }, { retries: 3, delayMs: 0 }),
    /not available/
  );
  assert.equal(soldOutCalls, 1);
  assert.equal(isTransient(new Error("Shop login failed. Check the account email and password.")), false);
  assert.equal(isTransient(new Error("checkout did not open")), true);

  const signal = new AbortController();
  signal.abort();
  await assert.rejects(() => withRetry("Submit order", async () => "nope", { signal: signal.signal, delayMs: 0 }), CheckoutStopped);
});

test("submit retries a missed click and stops after the order is accepted", () => {
  assert.equal(isOrderPost("https://www.toyc.com.hk/api/orders", "POST"), true);
  assert.equal(isOrderPost("https://www.toyc.com.hk/api/merchants/x/cart/items", "POST"), false);
  assert.equal(classifySubmit({ leftCheckout: false, orderAccepted: false, status: 0, message: "" }), "retry");
  assert.equal(classifySubmit({ leftCheckout: false, orderAccepted: true, status: 201, message: "" }), "done");
  assert.equal(classifySubmit({ leftCheckout: true, orderAccepted: false, status: 0, message: "" }), "done");
  assert.equal(classifySubmit({ leftCheckout: false, orderAccepted: false, status: 422, message: "card declined" }), "stop");
  assert.equal(classifySubmit({ leftCheckout: false, orderAccepted: false, status: 500, message: "" }), "retry");
  assert.equal(classifySubmit({ leftCheckout: false, orderAccepted: false, status: 0, message: "請填寫必填欄位" }), "stop");
  assert.equal(isTransient(loginWallError(false)), false);
  assert.match(loginWallError(false).message, /member login/);
  assert.match(loginWallError(true).message, /did not open the cart/);
});

test("an account email without a password is rejected", () => {
  assert.throws(
    () =>
      ordersFromText(
        JSON.stringify({
          productUrl: "https://www.toyc.com.hk/products/example",
          customer: { name: "陳大文", email: "ada@example.com", phone: "51234567" },
          account: { email: "ada@example.com", password: "" },
        })
      ),
    /both be set/
  );
});
