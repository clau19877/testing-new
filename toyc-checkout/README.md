# TOYC checkout

Guest checkout for [toyc.com.hk](https://www.toyc.com.hk/). The script uses the public storefront: product page, cart, then the customer form. It stops before **提交訂單** unless `submitOrder` is true.

A paid order cannot be cancelled. Card values stay in environment variables and are sent only when you submit.

## Run

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
