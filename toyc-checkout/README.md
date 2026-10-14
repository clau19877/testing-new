# TOYC checkout

Checkout desk for [toyc.com.hk](https://www.toyc.com.hk/). The panel imports order details, signs in to a shop account when the file includes one, and starts or stops the run with one button. It uses the public storefront and stops before **提交訂單** unless `submitOrder` is true.

A paid order cannot be cancelled. Card values stay in environment variables and are sent only when you submit.

## Panel

```bash
cd toyc-checkout
npm install
npm start
```

Open http://127.0.0.1:8787. The first visit creates the operator account. After sign-in, the Downloads list has the sample JSON, the sample CSV, the selected order file, the run log, and any checkout screenshots. Add `account.email` and `account.password` to an imported file to sign in at the shop before adding the item.

**Start** begins that order. The same button becomes **Stop** while Chrome is working and closes the browser immediately.

## Command line

```bash
cd toyc-checkout
npm install
cp config.example.json config.json
# edit customer name, email, Hong Kong mobile, product URL, and SF Express pickup
node checkout.js config.json
```

Chrome is detected from `CHROME_PATH` or `/usr/local/bin/google-chrome`. Set `headless` to false in the config to watch the browser.

To place the order, set `submitOrder` and `agreeTerms` to true and provide:

```bash
TOYC_CARD_NUMBER=... TOYC_CARD_NAME=... TOYC_CARD_EXPIRY=MM/YY TOYC_CARD_CVV=... node checkout.js config.json
```

Apple Pay stays on the device. Wallet methods that are not in the cart's payment list cannot be selected.

## What it fills

- quantity and an optional variation label
- delivery method, SF Express district and store, or a home-delivery address
- customer name, email, phone (`dialCode` defaults to 852), and remarks
- the 18+ terms checkbox

Shipping on the tested order was HK$50 under HK$500, to a SF Express station in Central.
