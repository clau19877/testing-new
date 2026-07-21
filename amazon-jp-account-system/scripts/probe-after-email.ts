import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

async function main() {
  mkdirSync("data/artifacts", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
  });
  const page = await context.newPage();
  const url =
    "https://www.amazon.co.jp/ap/register?openid.pape.max_auth_age=0" +
    "&openid.return_to=https%3A%2F%2Fwww.amazon.co.jp%2F%3Fref_%3Dnav_newcust" +
    "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select" +
    "&openid.assoc_handle=jpflex&openid.mode=checkid_setup" +
    "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select" +
    "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0";
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.fill("#ap_email_login", "probe-create-one-test@example.com");
  await page.locator('input[type="submit"]').first().click();
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(2000);
  await page.screenshot({
    path: "data/artifacts/probe-after-email.png",
    fullPage: true,
  });
  const info = await page.evaluate(() => {
    const inputs = [...document.querySelectorAll("input,button")].map((el) => ({
      tag: el.tagName,
      type: el.getAttribute("type"),
      id: el.id,
      name: el.getAttribute("name"),
      aria: el.getAttribute("aria-label"),
      visible: !!(el as HTMLElement).offsetWidth || !!(el as HTMLElement).offsetHeight,
    }));
    return {
      title: document.title,
      url: location.href,
      text: document.body.innerText.slice(0, 1200),
      inputs,
    };
  });
  console.log(JSON.stringify(info, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
