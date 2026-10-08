// A vendor's name as both registers spell it — case, punctuation and the company suffix aside.
// The compliance register names vendors as their certificates do; the Vendors page as the
// contract does. "Apex Lifts Ltd" and "APEX LIFTS" are one vendor.
export function vendorKey(name) {
  return String(name || "").toLowerCase().replace(/&/g, " and ")
    .replace(/\b(ltd|limited|llc|plc|inc|co)\b\.?/g, " ").replace(/[^a-z0-9]+/g, " ").trim();
}
