const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

function operatorPath(dataDir) {
  return path.join(dataDir, "operator.json");
}

function readOperator(dataDir) {
  const file = operatorPath(dataDir);
  if (!fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function hashPassword(password, salt = crypto.randomBytes(16)) {
  const hash = crypto.scryptSync(password, salt, 32);
  return { salt: salt.toString("hex"), hash: hash.toString("hex") };
}

function verifyPassword(password, operator) {
  const salt = Buffer.from(operator.salt, "hex");
  const expected = Buffer.from(operator.hash, "hex");
  const actual = crypto.scryptSync(password, salt, expected.length);
  return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
}

function createOperator(dataDir, username, password) {
  const name = String(username || "").trim();
  if (name.length < 3) throw new Error("Username must be at least 3 characters");
  if (String(password || "").length < 8) throw new Error("Password must be at least 8 characters");
  if (readOperator(dataDir)) throw new Error("An operator account already exists");
  fs.mkdirSync(dataDir, { recursive: true });
  const operator = { username: name, ...hashPassword(password) };
  fs.writeFileSync(operatorPath(dataDir), JSON.stringify(operator, null, 2));
  return { username: name };
}

function authenticate(dataDir, username, password) {
  const operator = readOperator(dataDir);
  if (!operator || operator.username !== String(username || "").trim()) return null;
  if (!verifyPassword(password, operator)) return null;
  return { username: operator.username };
}

module.exports = { readOperator, createOperator, authenticate };
