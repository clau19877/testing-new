const { normalizeOrder } = require("../checkout");

const CSV_FIELDS = {
  producturl: "productUrl",
  quantity: "quantity",
  variation: "variation",
  deliverymethod: "deliveryMethod",
  district: "district",
  storecontains: "storeContains",
  paymentmethod: "paymentMethod",
  customername: "customer.name",
  customeremail: "customer.email",
  customerphone: "customer.phone",
  dialcode: "customer.dialCode",
  gender: "customer.gender",
  remarks: "customer.remarks",
  accountemail: "account.email",
  accountpassword: "account.password",
  submitorder: "submitOrder",
  agreeterms: "agreeTerms",
  headless: "headless",
};

function setPath(target, path, value) {
  const parts = path.split(".");
  let cursor = target;
  for (let i = 0; i < parts.length - 1; i += 1) {
    cursor[parts[i]] = cursor[parts[i]] || {};
    cursor = cursor[parts[i]];
  }
  cursor[parts[parts.length - 1]] = value;
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  const source = text.replace(/^\uFEFF/, "");
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    if (quoted) {
      if (ch === '"' && source[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else if (ch !== "\r") cell += ch;
  }
  if (cell.length || row.length) {
    row.push(cell);
    rows.push(row);
  }
  const filled = rows.filter((entry) => entry.some((value) => String(value).trim()));
  if (filled.length < 2) throw new Error("CSV needs a header row and one order");
  const header = filled[0].map((name) => CSV_FIELDS[name.trim().toLowerCase()]);
  if (header.some((name) => !name)) {
    throw new Error(`CSV headers must include productUrl, customerName, customerEmail, and customerPhone`);
  }
  return filled.slice(1).map((values) => {
    const order = {};
    header.forEach((path, index) => {
      const raw = values[index] === undefined ? "" : String(values[index]).trim();
      if (!raw) return;
      if (path === "quantity") setPath(order, path, Number(raw));
      else if (path === "submitOrder" || path === "agreeTerms" || path === "headless") {
        setPath(order, path, /^(1|true|yes)$/i.test(raw));
      } else setPath(order, path, raw);
    });
    return order;
  });
}

function ordersFromText(text, filename = "") {
  const trimmed = text.trim();
  if (!trimmed) throw new Error("The imported file is empty");
  const looksCsv = filename.toLowerCase().endsWith(".csv") || (!trimmed.startsWith("{") && !trimmed.startsWith("["));
  const rawOrders = looksCsv ? parseCsv(trimmed) : asOrderList(JSON.parse(trimmed));
  return rawOrders.map((order, index) => {
    try {
      return normalizeOrder(order);
    } catch (error) {
      throw new Error(`Order ${index + 1}: ${error.message}`);
    }
  });
}

function asOrderList(parsed) {
  if (Array.isArray(parsed)) return parsed;
  if (parsed && Array.isArray(parsed.orders)) return parsed.orders;
  if (parsed && typeof parsed === "object") return [parsed];
  throw new Error("JSON must be an order object or an array of orders");
}

function publicOrder(record) {
  const config = record.config;
  return {
    id: record.id,
    createdAt: record.createdAt,
    sourceName: record.sourceName,
    productUrl: config.productUrl,
    quantity: config.quantity,
    variation: config.variation || "",
    deliveryMethod: config.deliveryMethod || "",
    district: config.district || "",
    storeContains: config.storeContains || "",
    paymentMethod: config.paymentMethod || "",
    customerName: config.customer.name,
    customerEmail: config.customer.email,
    customerPhone: config.customer.phone,
    accountEmail: config.account?.email || "",
    submitOrder: config.submitOrder === true,
  };
}

module.exports = { ordersFromText, publicOrder };
