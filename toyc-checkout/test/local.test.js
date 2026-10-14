const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { createOperator, authenticate, readOperator } = require("../lib/auth");
const { ordersFromText, publicOrder } = require("../lib/orders");

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
