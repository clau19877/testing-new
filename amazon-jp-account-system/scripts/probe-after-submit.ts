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
  await page.fill("#ap_email_login", `probe-${Date.now()}@example.com`);
  await page.locator('input[type="submit"]').first().click();
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1200);
  await page.locator('input[type="submit"]').first().click();
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1200);

  await page.fill("#ap_customer_name", "山田 太郎");
  await page.fill("#ap_password", "ProbePass123!");
  await page.fill("#ap_password_check", "ProbePass123!");
  await page.click("#continue");
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(2500);
  await page.screenshot({
    path: "data/artifacts/probe-after-submit.png",
    fullPage: true,
  });

  const info = await page.evaluate(() => ({
    title: document.title,
    url: location.href,
    text: document.body.innerText.slice(0, 1500),
    inputs: [...document.querySelectorAll("input")].map((el) => ({
      id: el.id,
      name: el.name,
      type: el.type,
      visible: !!(el.offsetWidth || el.offsetHeight),
    })),
  }));
  console.log(JSON.stringify(info, null, 2));
  await browser.close();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
