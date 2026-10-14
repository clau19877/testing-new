#!/usr/bin/env node
/**
 * Guest checkout for https://www.toyc.com.hk/ (SHOPLINE).
 *
 * Usage:
 *   cp config.example.json config.json
 *   # fill customer name, email, and Hong Kong phone
 *   npm install
 *   node checkout.js config.json
 *
 * submitOrder stays false unless you set it true. A submitted card order is
 * charged immediately, and TOYC does not allow cancellation after payment.
 * Card values are read from the environment only when submitOrder is true:
 *   TOYC_CARD_NUMBER, TOYC_CARD_NAME, TOYC_CARD_EXPIRY, TOYC_CARD_CVV
 *
 * Pages continue as soon as the next control is ready. A timed-out or
 * incomplete step is retried. A confirmed order POST is never submitted twice.
 * retries (1-5, default 3) and retryDelayMs (0-5000, default 300) are optional.
 */

const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

const CHROME =
  process.env.CHROME_PATH ||
  process.env.PUPPETEER_EXECUTABLE_PATH ||
  "/usr/local/bin/google-chrome";

class CheckoutStopped extends Error {
  constructor() {
    super("Checkout stopped");
    this.name = "CheckoutStopped";
    this.stopped = true;
  }
}

class CheckoutError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = "CheckoutError";
    this.retryable = options.retryable !== false;
  }
}

function sleep(ms, signal) {
  if (!ms) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(finish, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new CheckoutStopped());
    };
    function finish() {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }
    if (signal?.aborted) {
      clearTimeout(timer);
      reject(new CheckoutStopped());
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function checkpoint(signal) {
  if (signal?.aborted) throw new CheckoutStopped();
}

function boundedInt(value, fallback, min, max, label) {
  if (value === undefined || value === null || value === "") return fallback;
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) {
    throw new Error(`${label} must be an integer from ${min} to ${max}`);
  }
  return number;
}

function isTransient(error) {
  if (!error || error.stopped) return false;
  if (error.retryable === false) return false;
  const message = String(error.message || "");
  if (
    /not available|variation not found|Shop login failed|Apple Pay|card checkout needs|must both be set|agreeTerms must be true|rejected the order/i.test(
      message
    )
  ) {
    return false;
  }
  return true;
}

async function withRetry(label, fn, options = {}) {
  const retries = options.retries ?? 3;
  const delayMs = options.delayMs ?? 300;
  const signal = options.signal;
  const onStatus = options.onStatus || (() => {});
  let last;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    checkpoint(signal);
    try {
      return await fn(attempt);
    } catch (error) {
      if (signal?.aborted || error?.stopped) throw error?.stopped ? error : new CheckoutStopped();
      last = error;
      if (!isTransient(error) || attempt === retries) break;
      onStatus(`${label} failed, retrying (${attempt} of ${retries}): ${error.message}`);
      options.onRetry?.();
      await sleep(delayMs * attempt, signal);
    }
  }
  throw last;
}

function isOrderPost(url, method) {
  if (method !== "POST") return false;
  try {
    return /\/orders(\/|$)/.test(new URL(url).pathname);
  } catch {
    return false;
  }
}

function classifySubmit(outcome) {
  if (outcome.leftCheckout || outcome.orderAccepted) return "done";
  const status = outcome.status || 0;
  if (status >= 400 && status < 500 && status !== 408 && status !== 429) return "stop";
  const message = outcome.message || "";
  if (/卡號|信用卡|card|拒絕|declined|invalid|不正確|請填|必須|條款|售完|庫存不足/i.test(message)) return "stop";
  return "retry";
}

function normalizeOrder(config) {
  const accountEmail = config.account?.email?.trim?.() || config.account?.email || "";
  const accountPassword = config.account?.password || "";
  if ((accountEmail && !accountPassword) || (!accountEmail && accountPassword)) {
    throw new Error("account.email and account.password must both be set to sign in");
  }
  if (accountEmail) {
    config.account = { email: accountEmail, password: accountPassword };
  } else {
    delete config.account;
  }
  if (!config.productUrl) throw new Error("config.productUrl is required");
  if (!config.customer?.name || !config.customer?.email || !config.customer?.phone) {
    throw new Error("config.customer.name, email, and phone are required");
  }
  if (config.submitOrder && config.agreeTerms !== true) {
    throw new Error("agreeTerms must be true before an order can be submitted");
  }
  config.quantity =
    config.quantity === undefined || config.quantity === null || config.quantity === ""
      ? 1
      : Number(config.quantity);
  if (!Number.isInteger(config.quantity) || config.quantity < 1 || config.quantity > 20) {
    throw new Error("quantity must be an integer from 1 to 20");
  }
  config.retries = boundedInt(config.retries, 3, 1, 5, "retries");
  config.retryDelayMs = boundedInt(config.retryDelayMs, 300, 0, 5000, "retryDelayMs");
  return config;
}

function requireConfig(file) {
  return normalizeOrder(JSON.parse(fs.readFileSync(file, "utf8")));
}

async function shot(page, dir, name) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  await page.screenshot({ path: file });
  return file;
}

async function selectByLabel(page, selector, label, signal) {
  const deadline = Date.now() + 10000;
  let lastChoices = "";
  let stable = 0;
  while (Date.now() < deadline) {
    checkpoint(signal);
    const found = await page.evaluate(
      (sel, wanted) => {
        const el = document.querySelector(sel);
        if (!el) return { error: `missing select ${sel}` };
        const options = [...el.options].map((option) => ({
          value: option.value,
          text: option.text.trim(),
        }));
        const match = options.find((option) => option.text.includes(wanted) || option.value === wanted);
        if (!match) {
          return {
            error: `no option matching "${wanted}" in ${sel}`,
            choices: options.map((option) => option.text).filter(Boolean),
          };
        }
        return match;
      },
      selector,
      label
    );
    if (!found.error) {
      await page.select(selector, found.value);
      return found.text;
    }
    const choices = (found.choices || []).join("|");
    if (choices && choices === lastChoices && found.choices.length > 1) {
      stable += 1;
      if (stable >= 3) {
        throw new CheckoutError(`${found.error}\nchoices: ${found.choices.join(" | ")}`, { retryable: false });
      }
    } else {
      stable = 0;
    }
    lastChoices = choices;
    await sleep(80, signal);
  }
  throw new CheckoutError(`no option matching "${label}" in ${selector}`);
}

async function clickAddToCart(page, variation) {
  await page.waitForSelector("#btn-add-to-cart, .js-btn-main-add-to-cart", { timeout: 12000 });
  if (variation) {
    const picked = await page.evaluate((wanted) => {
      const nodes = [
        ...document.querySelectorAll(
          ".variation-label, .product-variant button, [class*='variant'] button, [class*='variation'] button, [class*='variation'] a"
        ),
      ];
      const match = nodes.find((node) => (node.innerText || "").includes(wanted));
      if (!match) return false;
      match.click();
      return true;
    }, variation);
    if (!picked) throw new CheckoutError(`variation not found: ${variation}`, { retryable: false });
  }

  const state = await page.evaluate(() => {
    const button = document.querySelector("#btn-add-to-cart");
    const text = (button?.innerText || "").replace(/\s+/g, " ").trim();
    return {
      text,
      disabled: !!button?.disabled,
      title: document.querySelector("h1")?.innerText?.trim() || document.title,
    };
  });
  if (/售完|售罄|已售完|Sold Out|暫不供貨/i.test(state.text)) {
    throw new CheckoutError(`item is not available (${state.text})`, { retryable: false });
  }
  if (!state.text || state.disabled) throw new CheckoutError("add-to-cart button was not ready");
  return state;
}

async function setQuantity(page, quantity) {
  if (quantity === 1) return;
  const set = await page.evaluate((quantity) => {
    const input = document.querySelector(
      'input[ng-model="addItemQuantity"], input[name="quantity"], .product-quantity input'
    );
    if (!input || typeof angular === "undefined") return false;
    const scope = angular.element(input).scope();
    scope.addItemQuantity = quantity;
    scope.$applyAsync();
    input.value = String(quantity);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }, quantity);
  if (!set) {
    for (let i = 1; i < quantity; i += 1) {
      await page.click('button[ng-click="incQuantity()"]');
    }
  }
}

async function fillInput(page, selector, value) {
  const text = String(value);
  await page.waitForSelector(selector, { visible: true, timeout: 10000 });
  const handle = await page.$(selector);
  await handle.click({ clickCount: 3 });
  await page.keyboard.press("Backspace");
  await handle.type(text, { delay: 0 });
  const current = await page.$eval(selector, (el) => el.value);
  if (current === text) return;
  if (/\d/.test(text) && current.replace(/\D/g, "") === text.replace(/\D/g, "")) return;
  await page.$eval(
    selector,
    (el, next) => {
      el.focus();
      el.value = next;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    },
    text
  );
}

async function setCheckbox(page, selector, checked) {
  const box = await page.$(selector);
  if (!box) return;
  const isChecked = await page.evaluate((sel) => document.querySelector(sel).checked, selector);
  if (isChecked !== checked) await page.click(selector);
}

async function setDialCode(page, phoneSelector, dialCode, signal) {
  const wanted = String(dialCode || "852").replace(/^\+/, "");
  const rootInfo = await page.evaluate((phoneSelector) => {
    const input = document.querySelector(phoneSelector);
    if (!input) return { error: "phone input missing" };
    const root = input.closest(".iti");
    if (!root) return { error: "phone country widget not found" };
    return { ok: true };
  }, phoneSelector);
  if (rootInfo.error) return rootInfo;

  const flag = await page.evaluateHandle(
    (phoneSelector) => document.querySelector(phoneSelector).closest(".iti").querySelector(".iti__selected-flag"),
    phoneSelector
  );
  await flag.asElement().click();
  await page.waitForFunction(
    (phoneSelector) => {
      const list = document.querySelector(phoneSelector)?.closest(".iti")?.querySelector(".iti__country-list");
      if (!list) return false;
      const style = getComputedStyle(list);
      return style.display !== "none" && style.visibility !== "hidden";
    },
    { timeout: 3000 },
    phoneSelector
  );

  const option = await page.evaluateHandle(
    (phoneSelector, wanted) => {
      const list = document.querySelector(phoneSelector).closest(".iti").querySelector(".iti__country-list");
      return [...list.querySelectorAll(".iti__country")].find((item) => item.getAttribute("data-dial-code") === wanted);
    },
    phoneSelector,
    wanted
  );
  const optionElement = option.asElement();
  if (!optionElement) return { error: `+${wanted} is not in the country list` };
  await optionElement.evaluate((node) => node.scrollIntoView({ block: "center" }));
  try {
    await optionElement.click();
  } catch {
    await optionElement.evaluate((node) => node.click());
  }
  await page
    .waitForFunction(
      (phoneSelector, wanted) => {
        const text =
          document.querySelector(phoneSelector)?.closest(".iti")?.querySelector(".iti__selected-dial-code")?.innerText?.trim() ||
          "";
        return text === `+${wanted}`;
      },
      { timeout: 3000 },
      phoneSelector,
      wanted
    )
    .catch(() => {});
  checkpoint(signal);
  const text = await page.evaluate(
    (phoneSelector) =>
      document.querySelector(phoneSelector).closest(".iti").querySelector(".iti__selected-dial-code")?.innerText?.trim() || "",
    phoneSelector
  );
  if (text !== `+${wanted}`) return { error: `dial code stayed ${text || "unchanged"}` };
  return { via: "iti", text };
}

async function fillVisibleAddress(page, config) {
  const home = /直送|地址/.test(config.deliveryMethod || "");
  if (!home) return { skipped: true };
  const address = config.address || {};
  if (!address.line1) throw new CheckoutError("address.line1 is required for home delivery", { retryable: false });
  const filled = await page.evaluate((address) => {
    const visible = (el) => el && (el.offsetParent !== null || el.getClientRects().length > 0);
    const inputs = [...document.querySelectorAll("input, textarea")].filter(visible);
    const used = [];
    const write = (pattern, value) => {
      if (!value) return;
      const input = inputs.find(
        (el) =>
          pattern.test(el.name || "") ||
          pattern.test(el.id || "") ||
          pattern.test(el.placeholder || "") ||
          pattern.test(el.getAttribute("aria-label") || "")
      );
      if (!input) return;
      input.focus();
      input.value = value;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      used.push(input.name || input.id || input.placeholder);
    };
    write(/address_1|address1|地址1|送貨地址/i, address.line1);
    write(/address_2|address2|地址2/i, address.line2);
    write(/city|城市/i, address.city);
    return used;
  }, address);
  if (!filled.length) throw new CheckoutError("home delivery was selected but no address fields were visible");
  return { filled };
}

function paymentFrameRole(url) {
  return new URLSearchParams(url.split("#")[1] || "").get("name");
}

async function fillCard(page, card) {
  const values = {
    number: card.number,
    firstName: card.name,
    expDate: card.expiry,
    cvc: card.cvv,
  };
  const autocomplete = {
    number: "cc-number",
    firstName: "cc-name",
    expDate: "cc-exp",
    cvc: "cc-csc",
  };
  const filled = [];
  for (const frame of page.frames()) {
    const role = paymentFrameRole(frame.url());
    if (!values[role]) continue;
    try {
      const handle = await frame.evaluateHandle((role, autocomplete) => {
        const inputs = [...document.querySelectorAll("input")];
        return (
          inputs.find((el) => el.autocomplete === autocomplete && !el.id.endsWith("-hidden")) ||
          inputs.find((el) => el.id === "input-file") ||
          null
        );
      }, role, autocomplete[role]);
      const element = handle.asElement();
      if (!element) continue;
      await element.click({ clickCount: 3 });
      await element.type(values[role], { delay: 0 });
      filled.push(role);
    } catch {
      // A PCI frame can detach while the checkout form re-renders.
    }
  }
  return filled;
}

function loginWallError(signedIn) {
  if (signedIn) {
    return new CheckoutError("Shop login did not open the cart. Check the account email and password.", {
      retryable: false,
    });
  }
  return new CheckoutError(
    "The store is asking for a member login before checkout. Add account.email and account.password to the order.",
    { retryable: false }
  );
}

async function gotoReady(page, url, selector, options = {}) {
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
  if (page.url().includes("/users/sign_in")) throw loginWallError(options.signedIn);
  try {
    await page.waitForSelector(selector, { timeout: 12000 });
  } catch (error) {
    throw new CheckoutError(`${error.message} (now at ${page.url()})`);
  }
}

async function loginToStore(page, account) {
  await page.goto("https://www.toyc.com.hk/users/sign_in", {
    waitUntil: "domcontentloaded",
    timeout: 20000,
  });
  const email = await page.waitForSelector('[data-e2e-id="login-email_input"]', { timeout: 12000 });
  await email.click({ clickCount: 3 });
  await email.type(account.email, { delay: 0 });
  const password = await page.waitForSelector('[data-e2e-id="login-password_input"]', { timeout: 12000 });
  await password.click({ clickCount: 3 });
  await password.type(account.password, { delay: 0 });
  const responsePromise = page.waitForResponse(
    (response) => response.url().includes("/api/users/sign_in") && response.request().method() === "POST",
    { timeout: 12000 }
  );
  const clicked = await page.evaluate(() => {
    const button = document.querySelector('[data-e2e-id="login-submit_button"]');
    if (!button) return false;
    button.click();
    return true;
  });
  if (!clicked) throw new CheckoutError("Shop sign-in button was not found");
  const response = await responsePromise;
  const body = await response.text();
  let payload = null;
  try {
    payload = body ? JSON.parse(body) : null;
  } catch {
    payload = null;
  }
  if (!payload || response.status() < 200 || response.status() >= 300) {
    throw new CheckoutError("Shop login failed. Check the account email and password.", { retryable: false });
  }
  await page.waitForFunction(() => !location.href.includes("/users/sign_in"), { timeout: 10000 });
  return { email: account.email, url: page.url() };
}

async function addItem(page, signal, state) {
  if (state.posted) return;
  if (state.clicked) {
    await sleep(400, signal);
    if (state.posted) return;
  }
  const pending = page.waitForResponse(
    (response) => response.url().includes("/cart/items") && response.request().method() === "POST",
    { timeout: 12000 }
  );
  state.clicked = true;
  checkpoint(signal);
  await page.click("#btn-add-to-cart");
  const response = await pending;
  if (response.ok()) {
    state.posted = true;
    return;
  }
  const permanent = response.status() >= 400 && response.status() < 500;
  throw new CheckoutError(`add to cart failed (${response.status()})`, { retryable: !permanent });
}

async function prepareCheckout(page, config, product, signal) {
  await gotoReady(page, "https://www.toyc.com.hk/cart", "#order-delivery-method", {
    signedIn: Boolean(config.account?.email),
  });
  const cartText = await page.evaluate(() => document.body.innerText);
  if (!cartText.includes(product.title.slice(0, 12))) throw new CheckoutError("cart does not contain the product");

  const deliveryMethod = await selectByLabel(page, "#order-delivery-method", config.deliveryMethod || "順豐站取件", signal);
  const sf = /順豐|sfexpress|SF/i.test(deliveryMethod);
  let district;
  let store;
  if (config.district && sf) {
    district = await selectByLabel(page, 'select[name="delivery[sfexpress][district]"]', config.district, signal);
  }
  if (config.storeContains && sf) {
    store = await selectByLabel(page, 'select[name="delivery[sfexpress][store]"]', config.storeContains, signal);
  }
  const address = await fillVisibleAddress(page, config);
  const paymentMethod = await selectByLabel(page, "#order-payment-method", config.paymentMethod || "信用卡", signal);
  const checkoutHref = await page.evaluate(() => {
    const link = [...document.querySelectorAll("a")].find((anchor) => (anchor.innerText || "").includes("前往結帳"));
    return link ? link.href : null;
  });
  if (!checkoutHref) throw new CheckoutError("cart page has no 前往結帳 link");
  await gotoReady(page, checkoutHref, "#order-customer-name", {
    signedIn: Boolean(config.account?.email),
  });
  if (!page.url().includes("/checkout")) throw new CheckoutError(`checkout did not open (now at ${page.url()})`);
  return { deliveryMethod, district, store, address, paymentMethod };
}

async function fillCheckout(page, config, signal) {
  await fillInput(page, "#order-customer-name", config.customer.name);
  await fillInput(page, "#order-customer-email", config.customer.email);
  await fillInput(page, "#order-customer-phone", config.customer.phone);
  const dialCode = await setDialCode(page, "#order-customer-phone", config.customer.dialCode || "852", signal);
  if (dialCode.error) throw new CheckoutError(dialCode.error);
  let gender;
  if (config.customer.gender) gender = await selectByLabel(page, "#order-customer-gender", config.customer.gender, signal);
  if (config.customer.remarks) await fillInput(page, "#order-remarks", config.customer.remarks);

  await setCheckbox(page, 'input[name="order[delivery_data][recipient_is_customer]"]', config.recipientSameAsCustomer !== false);
  const recipientPhone = await page.$("#recipient-phone");
  let recipientDialCode;
  if (recipientPhone) {
    if (config.recipientSameAsCustomer === false) {
      await fillInput(page, "#recipient-name", config.address?.recipientName || config.customer.name);
      await fillInput(page, "#recipient-phone", config.address?.phone || config.customer.phone);
    }
    recipientDialCode = await setDialCode(page, "#recipient-phone", config.customer.dialCode || "852", signal);
    if (recipientDialCode.error) throw new CheckoutError(`recipient phone: ${recipientDialCode.error}`);
  }

  await setCheckbox(page, 'input[name="policy"]', config.agreeTerms === true);
  await setCheckbox(page, 'input[name="saveFields[all]"]', config.joinMembership === true);
  await setCheckbox(page, 'input[name="saveFields[marketing]"]', config.marketing === true);

  const review = await page.evaluate(() => ({
    url: location.href,
    name: document.querySelector("#order-customer-name")?.value || "",
    email: document.querySelector("#order-customer-email")?.value || "",
    phone: document.querySelector("#order-customer-phone")?.value || "",
    dialLabel: (() => {
      const input = document.querySelector("#order-customer-phone");
      const group = input?.closest(".form-group, .iti") || input?.parentElement?.parentElement;
      const flag = group?.querySelector(".iti__selected-dial-code, .selected-dial-code");
      if (flag) return flag.innerText.trim();
      const text = (group?.innerText || "").replace(/\s+/g, " ");
      return (text.match(/[A-Z]{2}\s*\+\d+|\+\d+/) || [""])[0];
    })(),
    policy: !!document.querySelector('input[name="policy"]')?.checked,
    total: (document.body.innerText.match(/合計:\s*HK\$[\d,.]+/) || [])[0] || "",
    submitVisible: [...document.querySelectorAll("button, a, input")].some((el) => /提交訂單/.test(el.innerText || el.value || "")),
  }));
  return { review, gender, dialCode, recipientDialCode };
}

async function captureCheckout(page, screenshotDir, detailed) {
  const files = [];
  const snap = async (name, scroll) => {
    if (scroll) await page.evaluate(scroll);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve())));
    files.push(await shot(page, screenshotDir, name));
  };
  await snap("05-terms.png", () => {
    document.querySelector('input[name="policy"]')?.scrollIntoView({ block: "center" });
  });
  if (!detailed) return files;
  await snap("03-customer.png", () => {
    document.querySelector("#order-customer-name")?.scrollIntoView({ block: "center" });
  });
  await snap("04-payment.png", () => {
    const heading = [...document.querySelectorAll("h1,h2,h3,h4,div,span,label")].find(
      (el) => (el.innerText || "").trim() === "付款資料"
    );
    heading?.scrollIntoView({ block: "center" });
  });
  return files;
}

async function readSubmitMessage(page) {
  return page.evaluate(() => {
    const alert = document.querySelector(".alert-danger, .toast-error, .error, [role='alert']");
    return (alert?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 240);
  });
}

async function clickSubmit(page) {
  const clicked = await page.evaluate(() => {
    const button = [...document.querySelectorAll("button, a, input")].find((el) => /提交訂單/.test(el.innerText || el.value || ""));
    if (!button || button.disabled) return false;
    button.click();
    return true;
  });
  if (!clicked) throw new CheckoutError("提交訂單 button was not found");
}

async function submitOrder(page, options) {
  const { signal, onStatus, retries, delayMs, onRetry } = options;
  let orderStatus = 0;
  let orderStarted = false;
  const track = (request) => {
    if (isOrderPost(request.url(), request.method())) orderStarted = true;
  };
  const trackResponse = (response) => {
    if (!isOrderPost(response.url(), response.request().method())) return;
    orderStatus = response.status();
  };
  const trackFailed = (request) => {
    if (!isOrderPost(request.url(), request.method())) return;
    if (!orderStatus) orderStarted = false;
  };
  page.on("request", track);
  page.on("response", trackResponse);
  page.on("requestfailed", trackFailed);
  try {
    await withRetry(
      "Submit order",
      async () => {
        checkpoint(signal);
        if ((orderStatus >= 200 && orderStatus < 300) || !page.url().includes("/checkout")) return;
        if (!(orderStarted && !orderStatus)) await clickSubmit(page);
        const deadline = Date.now() + 12000;
        while (Date.now() < deadline) {
          checkpoint(signal);
          const leftCheckout = !page.url().includes("/checkout");
          const orderAccepted = orderStatus >= 200 && orderStatus < 300;
          if (orderStatus || leftCheckout) {
            const message = await readSubmitMessage(page);
            const decision = classifySubmit({ leftCheckout, orderAccepted, status: orderStatus, message });
            if (decision === "done") {
              await page.waitForFunction(() => !location.pathname.includes("/checkout"), { timeout: 2000 }).catch(() => {});
              return;
            }
            if (decision === "stop") {
              throw new CheckoutError(message || `The store rejected the order (${orderStatus})`, { retryable: false });
            }
            throw new CheckoutError(message || "The store did not confirm the order");
          }
          const message = await readSubmitMessage(page);
          if (message && classifySubmit({ leftCheckout: false, orderAccepted: false, status: 0, message }) === "stop") {
            throw new CheckoutError(message, { retryable: false });
          }
          await sleep(120, signal);
        }
        throw new CheckoutError(orderStarted ? "The order request did not finish" : "The store did not confirm the order");
      },
      { retries, delayMs, signal, onStatus, onRetry }
    );
  } finally {
    page.off("request", track);
    page.off("response", trackResponse);
    page.off("requestfailed", trackFailed);
  }
}

async function run(config, screenshotDir, options = {}) {
  const signal = options.signal;
  const onStatus = typeof options.onStatus === "function" ? options.onStatus : () => {};
  const retries = boundedInt(config.retries, 3, 1, 5, "retries");
  const delayMs = boundedInt(config.retryDelayMs, 300, 0, 5000, "retryDelayMs");
  const startedAt = Date.now();
  const retryState = { used: 0 };
  const retryOptions = {
    retries,
    delayMs,
    signal,
    onStatus,
    onRetry: () => {
      retryState.used += 1;
    },
  };
  checkpoint(signal);
  let browser;
  const closeBrowser = () => browser?.close().catch(() => {});
  if (signal) signal.addEventListener("abort", closeBrowser, { once: true });
  browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: config.headless !== false ? "new" : false,
    args: [
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--disable-extensions",
      "--disable-background-networking",
      "--disable-sync",
      "--disable-default-apps",
      "--no-first-run",
      "--mute-audio",
      "--window-size=1440,1200",
    ],
  });
  if (signal?.aborted) {
    await closeBrowser();
    throw new CheckoutStopped();
  }
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1200 });
  page.setDefaultNavigationTimeout(20000);
  page.setDefaultTimeout(12000);
  const result = { submitted: false, stopped: false, retriesUsed: 0 };

  try {
    if (config.account?.email) {
      checkpoint(signal);
      onStatus("Signing in to the shop account");
      result.accountEmail = (
        await withRetry("Shop sign-in", () => loginToStore(page, config.account), retryOptions)
      ).email;
    }
    checkpoint(signal);
    onStatus("Opening the product page");
    const product = await withRetry(
      "Open product",
      async () => {
        await gotoReady(page, config.productUrl, "#btn-add-to-cart, .js-btn-main-add-to-cart", {
          signedIn: Boolean(config.account?.email),
        });
        return clickAddToCart(page, config.variation);
      },
      retryOptions
    );
    result.productTitle = product.title;
    await setQuantity(page, config.quantity);
    checkpoint(signal);
    onStatus("Adding the item to the cart");
    const cartState = { posted: false, clicked: false };
    const markPosted = (response) => {
      if (response.url().includes("/cart/items") && response.request().method() === "POST" && response.ok()) {
        cartState.posted = true;
      }
    };
    page.on("response", markPosted);
    try {
      await withRetry("Add to cart", () => addItem(page, signal, cartState), retryOptions);
    } finally {
      page.off("response", markPosted);
    }
    result.screenshots = [await shot(page, screenshotDir, "01-product.png")];

    checkpoint(signal);
    onStatus("Choosing delivery and payment");
    const prepared = await withRetry(
      "Delivery and checkout",
      async () => {
        const cart = await prepareCheckout(page, config, product, signal);
        await shot(page, screenshotDir, "02-cart.png");
        onStatus("Filling customer details");
        const filled = await fillCheckout(page, config, signal);
        return { cart, filled };
      },
      retryOptions
    );
    result.deliveryMethod = prepared.cart.deliveryMethod;
    result.district = prepared.cart.district;
    result.store = prepared.cart.store;
    result.address = prepared.cart.address;
    result.paymentMethod = prepared.cart.paymentMethod;
    result.gender = prepared.filled.gender;
    result.dialCode = prepared.filled.dialCode;
    result.recipientDialCode = prepared.filled.recipientDialCode;
    result.review = prepared.filled.review;
    result.paymentFrames = page
      .frames()
      .map((frame) => paymentFrameRole(frame.url()))
      .filter((role) => ["number", "firstName", "expDate", "cvc"].includes(role));

    try {
      const shots = await captureCheckout(page, screenshotDir, !config.submitOrder);
      result.screenshots.push(...shots);
    } catch {
      // A screenshot miss should not slow or stop the order.
    }

    result.retriesUsed = retryState.used;
    result.elapsedMs = Date.now() - startedAt;
    if (!config.submitOrder) {
      checkpoint(signal);
      onStatus(`Ready for review. Payment was not submitted. (${(result.elapsedMs / 1000).toFixed(1)}s)`);
      result.stoppedBeforeSubmit = true;
      return result;
    }

    if (/Apple Pay/i.test(result.paymentMethod || "")) {
      throw new CheckoutError("Apple Pay has to be confirmed on the device; the script stops before submit", { retryable: false });
    }
    if (/信用卡|VISA|Mastercard/i.test(result.paymentMethod || "")) {
      const card = {
        number: process.env.TOYC_CARD_NUMBER || "",
        name: process.env.TOYC_CARD_NAME || "",
        expiry: process.env.TOYC_CARD_EXPIRY || "",
        cvv: process.env.TOYC_CARD_CVV || "",
      };
      if (!card.number || !card.name || !card.expiry || !card.cvv) {
        throw new CheckoutError("card checkout needs TOYC_CARD_NUMBER, TOYC_CARD_NAME, TOYC_CARD_EXPIRY, and TOYC_CARD_CVV", {
          retryable: false,
        });
      }
      result.cardFilled = await withRetry("Card fields", async () => {
        const filled = await fillCard(page, card);
        if (filled.length < 4) throw new CheckoutError("card fields were not all filled; order was not submitted");
        return filled;
      }, retryOptions);
    }

    checkpoint(signal);
    onStatus("Submitting the order");
    await submitOrder(page, { ...retryOptions, retries, delayMs });
    result.submitted = true;
    result.confirmationUrl = page.url();
    result.elapsedMs = Date.now() - startedAt;
    result.retriesUsed = retryState.used;
    try {
      result.screenshots.push(await shot(page, screenshotDir, "04-after-submit.png"));
    } catch {
      // The confirmation page can replace the document while the shot is taken.
    }
    onStatus(`Order submitted. (${(result.elapsedMs / 1000).toFixed(1)}s)`);
    return result;
  } catch (error) {
    result.elapsedMs = Date.now() - startedAt;
    result.retriesUsed = retryState.used;
    if (signal?.aborted || error.stopped) {
      result.stopped = true;
      result.error = "Stopped";
      onStatus("Stopped");
      return result;
    }
    result.error = error.message;
    try {
      result.screenshots = result.screenshots || [];
      result.screenshots.push(await shot(page, screenshotDir, "error.png"));
    } catch {
      // Screenshot can fail if the browser already closed.
    }
    throw Object.assign(error, { result });
  } finally {
    signal?.removeEventListener("abort", closeBrowser);
    await closeBrowser();
  }
}

async function main() {
  const configPath = path.resolve(process.argv[2] || "config.json");
  const config = requireConfig(configPath);
  const screenshotDir = path.resolve(process.env.TOYC_SCREENSHOT_DIR || path.join(path.dirname(configPath), "screenshots"));
  try {
    const result = await run(config, screenshotDir);
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(error.message);
    if (error.result) console.error(JSON.stringify(error.result, null, 2));
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  run,
  requireConfig,
  normalizeOrder,
  loginToStore,
  CheckoutStopped,
  CheckoutError,
  withRetry,
  isTransient,
  isOrderPost,
  classifySubmit,
  loginWallError,
};
