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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function checkpoint(signal) {
  if (signal?.aborted) throw new CheckoutStopped();
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
  return config;
}

function requireConfig(file) {
  return normalizeOrder(JSON.parse(fs.readFileSync(file, "utf8")));
}

async function shot(page, dir, name) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

async function selectByLabel(page, selector, label) {
  const found = await page.evaluate(
    (sel, wanted) => {
      const el = document.querySelector(sel);
      if (!el) return { error: `missing select ${sel}` };
      const options = [...el.options].map((option) => ({
        value: option.value,
        text: option.text.trim(),
      }));
      const match = options.find(
        (option) => option.text.includes(wanted) || option.value === wanted
      );
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
  if (found.error) {
    const choices = found.choices ? `\nchoices: ${found.choices.join(" | ")}` : "";
    throw new Error(found.error + choices);
  }
  await page.select(selector, found.value);
  await sleep(600);
  return found.text;
}

async function clickAddToCart(page, variation) {
  await page.waitForSelector("#btn-add-to-cart, .js-btn-main-add-to-cart", {
    timeout: 20000,
  });
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
    if (!picked) throw new Error(`variation not found: ${variation}`);
    await sleep(500);
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
  if (!state.text || /售完|售罄|已售完|Sold Out|暫不供貨/i.test(state.text) || state.disabled) {
    throw new Error(`item is not available (${state.text || "no add-to-cart button"})`);
  }
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
      await sleep(150);
    }
  }
}

async function fillInput(page, selector, value) {
  await page.waitForSelector(selector, { visible: true, timeout: 15000 });
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press("Backspace");
  await page.type(selector, String(value), { delay: 10 });
  await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }, selector);
}

async function setCheckbox(page, selector, checked) {
  const box = await page.$(selector);
  if (!box) return;
  const isChecked = await page.evaluate((sel) => document.querySelector(sel).checked, selector);
  if (isChecked !== checked) await page.click(selector);
}

async function setDialCode(page, phoneSelector, dialCode) {
  const wanted = String(dialCode || "852").replace(/^\+/, "");
  const rootInfo = await page.evaluate((phoneSelector) => {
    const input = document.querySelector(phoneSelector);
    if (!input) return { error: "phone input missing" };
    const root = input.closest(".iti");
    if (!root) {
      return {
        error: "phone country widget not found",
        html: (input.parentElement?.parentElement?.outerHTML || "").slice(0, 500),
      };
    }
    return { ok: true };
  }, phoneSelector);
  if (rootInfo.error) return rootInfo;

  const flag = await page.evaluateHandle(
    (phoneSelector) => document.querySelector(phoneSelector).closest(".iti").querySelector(".iti__selected-flag"),
    phoneSelector
  );
  await flag.asElement().click();
  await sleep(200);

  const option = await page.evaluateHandle(
    (phoneSelector, wanted) => {
      const list = document.querySelector(phoneSelector).closest(".iti").querySelector(".iti__country-list");
      return [...list.querySelectorAll(".iti__country")].find(
        (item) => item.getAttribute("data-dial-code") === wanted
      );
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
  await sleep(200);
  const text = await page.evaluate(
    (phoneSelector) =>
      document.querySelector(phoneSelector).closest(".iti").querySelector(".iti__selected-dial-code")?.innerText?.trim() ||
      "",
    phoneSelector
  );
  if (text !== `+${wanted}`) return { error: `dial code stayed ${text || "unchanged"}` };
  return { via: "iti", text };
}

async function fillVisibleAddress(page, config) {
  const home = /直送|地址/.test(config.deliveryMethod || "");
  if (!home) return { skipped: true };
  const address = config.address || {};
  if (!address.line1) throw new Error("address.line1 is required for home delivery");
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
  if (!filled.length) throw new Error("home delivery was selected but no address fields were visible");
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
      await element.type(values[role], { delay: 20 });
      filled.push(role);
    } catch {
      // A PCI frame can detach while the checkout form re-renders.
    }
  }
  return filled;
}

async function loginToStore(page, account) {
  await page.goto("https://www.toyc.com.hk/users/sign_in", {
    waitUntil: "networkidle2",
    timeout: 60000,
  });
  const email = await page.waitForSelector('[data-e2e-id="login-email_input"]', { timeout: 20000 });
  await email.click({ clickCount: 3 });
  await email.type(account.email, { delay: 12 });
  const password = await page.waitForSelector('[data-e2e-id="login-password_input"]', { timeout: 20000 });
  await password.click({ clickCount: 3 });
  await password.type(account.password, { delay: 12 });
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/users/sign_in") && response.request().method() === "POST",
    { timeout: 20000 }
  );
  const clicked = await page.evaluate(() => {
    const button = document.querySelector('[data-e2e-id="login-submit_button"]');
    if (!button) return false;
    button.click();
    return true;
  });
  if (!clicked) throw new Error("Shop sign-in button was not found");
  const response = await responsePromise;
  const body = await response.text();
  let payload = null;
  try {
    payload = body ? JSON.parse(body) : null;
  } catch {
    payload = null;
  }
  if (!payload || response.status() < 200 || response.status() >= 300) {
    throw new Error("Shop login failed. Check the account email and password.");
  }
  await page.waitForFunction(() => !location.href.includes("/users/sign_in"), { timeout: 15000 });
  return { email: account.email, url: page.url() };
}

async function run(config, screenshotDir, options = {}) {
  const signal = options.signal;
  const onStatus = typeof options.onStatus === "function" ? options.onStatus : () => {};
  checkpoint(signal);
  let browser;
  const closeBrowser = () => browser?.close().catch(() => {});
  if (signal) signal.addEventListener("abort", closeBrowser, { once: true });
  browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: config.headless !== false ? "new" : false,
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1440,1200"],
  });
  if (signal?.aborted) {
    await closeBrowser();
    throw new CheckoutStopped();
  }
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1200 });
  const result = { submitted: false, stopped: false };

  try {
    if (config.account?.email) {
      checkpoint(signal);
      onStatus("Signing in to the shop account");
      result.accountEmail = (await loginToStore(page, config.account)).email;
    }
    checkpoint(signal);
    onStatus("Opening the product page");
    await page.goto(config.productUrl, { waitUntil: "networkidle2", timeout: 60000 });
    const product = await clickAddToCart(page, config.variation);
    result.productTitle = product.title;
    await setQuantity(page, config.quantity);
    checkpoint(signal);
    onStatus("Adding the item to the cart");
    await page.click("#btn-add-to-cart");
    await sleep(2000);
    await shot(page, screenshotDir, "01-product.png");

    checkpoint(signal);
    onStatus("Choosing delivery and payment");
    await page.goto("https://www.toyc.com.hk/cart", { waitUntil: "networkidle2", timeout: 60000 });
    await page.waitForSelector("#order-delivery-method", { timeout: 20000 });
    const cartText = await page.evaluate(() => document.body.innerText);
    if (!cartText.includes(product.title.slice(0, 12))) {
      throw new Error("cart does not contain the product");
    }

    result.deliveryMethod = await selectByLabel(
      page,
      "#order-delivery-method",
      config.deliveryMethod || "順豐站取件"
    );
    await sleep(800);

    if (config.district) {
      const districtSelect = await page.$('select[name="delivery[sfexpress][district]"]');
      if (districtSelect) {
        result.district = await selectByLabel(
          page,
          'select[name="delivery[sfexpress][district]"]',
          config.district
        );
      }
    }
    if (config.storeContains) {
      const storeSelect = await page.$('select[name="delivery[sfexpress][store]"]');
      if (storeSelect) {
        result.store = await selectByLabel(
          page,
          'select[name="delivery[sfexpress][store]"]',
          config.storeContains
        );
      }
    }
    result.address = await fillVisibleAddress(page, config);
    result.paymentMethod = await selectByLabel(
      page,
      "#order-payment-method",
      config.paymentMethod || "信用卡"
    );
    await shot(page, screenshotDir, "02-cart.png");

    const checkoutHref = await page.evaluate(() => {
      const link = [...document.querySelectorAll("a")].find((anchor) =>
        (anchor.innerText || "").includes("前往結帳")
      );
      return link ? link.href : null;
    });
    if (!checkoutHref) throw new Error("cart page has no 前往結帳 link");
    checkpoint(signal);
    onStatus("Filling customer details");
    await page.goto(checkoutHref, { waitUntil: "networkidle2", timeout: 60000 });
    await page.waitForSelector("#order-customer-name", { timeout: 20000 });
    if (!page.url().includes("/checkout")) {
      throw new Error(`checkout did not open (now at ${page.url()})`);
    }

    await fillInput(page, "#order-customer-name", config.customer.name);
    await fillInput(page, "#order-customer-email", config.customer.email);
    await fillInput(page, "#order-customer-phone", config.customer.phone);
    result.dialCode = await setDialCode(page, "#order-customer-phone", config.customer.dialCode || "852");
    if (result.dialCode.error) throw new Error(result.dialCode.error);
    if (config.customer.gender) {
      result.gender = await selectByLabel(page, "#order-customer-gender", config.customer.gender);
    }
    if (config.customer.remarks) {
      await fillInput(page, "#order-remarks", config.customer.remarks);
    }

    await setCheckbox(
      page,
      'input[name="order[delivery_data][recipient_is_customer]"]',
      config.recipientSameAsCustomer !== false
    );
    const recipientPhone = await page.$("#recipient-phone");
    if (recipientPhone) {
      if (config.recipientSameAsCustomer === false) {
        await fillInput(page, "#recipient-name", config.address?.recipientName || config.customer.name);
        await fillInput(page, "#recipient-phone", config.address?.phone || config.customer.phone);
      }
      result.recipientDialCode = await setDialCode(page, "#recipient-phone", config.customer.dialCode || "852");
      if (result.recipientDialCode.error) throw new Error(`recipient phone: ${result.recipientDialCode.error}`);
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
      submitVisible: [...document.querySelectorAll("button, a, input")].some((el) =>
        /提交訂單/.test(el.innerText || el.value || "")
      ),
    }));
    result.review = review;
    result.paymentFrames = page
      .frames()
      .map((frame) => paymentFrameRole(frame.url()))
      .filter((role) => ["number", "firstName", "expDate", "cvc"].includes(role));
    await page.evaluate(() => document.querySelector('input[name="policy"]')?.scrollIntoView({ block: "center" }));
    await sleep(200);
    const termsShot = path.join(screenshotDir, "05-terms.png");
    await page.screenshot({ path: termsShot });
    await page.evaluate(() => document.querySelector("#order-customer-name")?.scrollIntoView({ block: "center" }));
    await sleep(300);
    const customerShot = path.join(screenshotDir, "03-customer.png");
    await page.screenshot({ path: customerShot });
    await page.evaluate(() => {
      const heading = [...document.querySelectorAll("h1,h2,h3,h4,div,span,label")].find(
        (el) => (el.innerText || "").trim() === "付款資料"
      );
      heading?.scrollIntoView({ block: "center" });
    });
    await sleep(300);
    const paymentShot = path.join(screenshotDir, "04-payment.png");
    await page.screenshot({ path: paymentShot });
    result.screenshots = [customerShot, paymentShot, termsShot];

    if (!config.submitOrder) {
      checkpoint(signal);
      onStatus("Ready for review. Payment was not submitted.");
      result.stoppedBeforeSubmit = true;
      return result;
    }

    if (/Apple Pay/i.test(result.paymentMethod)) {
      throw new Error("Apple Pay has to be confirmed on the device; the script stops before submit");
    }
    if (/信用卡|VISA|Mastercard/i.test(result.paymentMethod)) {
      const card = {
        number: process.env.TOYC_CARD_NUMBER || "",
        name: process.env.TOYC_CARD_NAME || "",
        expiry: process.env.TOYC_CARD_EXPIRY || "",
        cvv: process.env.TOYC_CARD_CVV || "",
      };
      if (!card.number || !card.name || !card.expiry || !card.cvv) {
        throw new Error("card checkout needs TOYC_CARD_NUMBER, TOYC_CARD_NAME, TOYC_CARD_EXPIRY, and TOYC_CARD_CVV");
      }
      result.cardFilled = await fillCard(page, card);
      if (result.cardFilled.length < 4) {
        throw new Error("card fields were not all filled; order was not submitted");
      }
    }

    const clicked = await page.evaluate(() => {
      const button = [...document.querySelectorAll("button, a, input")].find((el) =>
        /提交訂單/.test(el.innerText || el.value || "")
      );
      if (!button) return false;
      button.click();
      return true;
    });
    if (!clicked) throw new Error("提交訂單 button was not found");
    await sleep(4000);
    result.submitted = true;
    result.confirmationUrl = page.url();
    result.screenshots.push(await shot(page, screenshotDir, "04-after-submit.png"));
    return result;
  } catch (error) {
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
  const screenshotDir = path.resolve(
    process.env.TOYC_SCREENSHOT_DIR || path.join(path.dirname(configPath), "screenshots")
  );
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

module.exports = { run, requireConfig, normalizeOrder, loginToStore, CheckoutStopped };
