import assert from "node:assert/strict";
import { extractOtpFromText, isAmazonishSender } from "../src/utils/otp.js";

assert.equal(
  extractOtpFromText("Your Amazon verification code is 123456. Do not share it."),
  "123456",
);
assert.equal(
  extractOtpFromText("Amazon.co.jp 認証コード: 987654 を入力してください"),
  "987654",
);
assert.equal(
  extractOtpFromText("ワンタイムパスワード 445566 の有効期限は10分です"),
  "445566",
);
assert.equal(extractOtpFromText("no code here"), undefined);
assert.equal(isAmazonishSender("Amazon <account-update@amazon.co.jp>"), true);
assert.equal(isAmazonishSender("Friend <friend@example.com>"), false);

console.log("otp utils ok");
