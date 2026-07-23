const { COMMON_TYPOS } = require("./typos");

/**
 * Strip punctuation / separator noise while preserving word and number boundaries.
 *
 * Handles obfuscation patterns such as dots/underscores inserted inside words
 * ("Un.it" → "Unit") before turning remaining separators into spaces.
 */
function stripPunctuation(input) {
  return (
    String(input)
      .normalize("NFKC")
      // Drop dots/underscores inserted between letters (Un.it → Unit)
      .replace(/(?<=\p{L})[._]+(?=\p{L})/gu, "")
      // Everything else that isn't a letter/number becomes a space
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .replace(/\s+/g, " ")
      .trim()
  );
}

/**
 * Correct a single token using the typo dictionary.
 * Also expands glued forms like "unit13" -> "unit 13" when helpful.
 */
function correctToken(token, dictionary = COMMON_TYPOS) {
  const lower = token.toLowerCase();

  if (dictionary[lower]) {
    return dictionary[lower];
  }

  // Split glued letter+number (unit13, 13unit, feltroad)
  const glued = lower.match(/^([a-z]+)(\d+)$|^(\d+)([a-z]+)$/);
  if (glued) {
    const left = glued[1] || glued[3];
    const right = glued[2] || glued[4];
    const leftFixed = dictionary[left] || left;
    const rightFixed = dictionary[right] || right;
    // Prefer "unit 13" / "13 unit" ordering based on original sides
    if (glued[1]) {
      return `${leftFixed} ${right}`;
    }
    return `${left} ${rightFixed}`;
  }

  return lower;
}

/**
 * Apply typo corrections token-by-token.
 */
function correctTypos(text, dictionary = COMMON_TYPOS) {
  return stripPunctuation(text)
    .split(" ")
    .filter(Boolean)
    .map((token) => correctToken(token, dictionary))
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Normalize a free-text address line for comparison / storage.
 *
 * @param {string} addressLine
 * @param {{ dictionary?: Record<string, string>, uppercase?: boolean }} [options]
 * @returns {string} Canonical lowercase address text (unless uppercase: true)
 */
function normalizeAddress(addressLine, options = {}) {
  const { dictionary = COMMON_TYPOS, uppercase = false } = options;
  if (addressLine == null || String(addressLine).trim() === "") {
    return "";
  }

  const corrected = correctTypos(addressLine, dictionary);
  return uppercase ? corrected.toUpperCase() : corrected;
}

/**
 * Normalize multi-line address profiles into a single comparable string.
 *
 * @param {string | string[]} lines
 * @param {{ dictionary?: Record<string, string>, uppercase?: boolean, separator?: string }} [options]
 */
function normalizeAddressLines(lines, options = {}) {
  const { separator = " " } = options;
  const list = Array.isArray(lines) ? lines : String(lines).split(/\r?\n/);
  return list
    .map((line) => normalizeAddress(line, options))
    .filter(Boolean)
    .join(separator)
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Compare two addresses after normalization.
 */
function addressesMatch(a, b, options = {}) {
  return normalizeAddressLines(a, options) === normalizeAddressLines(b, options);
}

/**
 * Build a dictionary from COMMON_TYPOS plus caller-supplied aliases
 * (e.g. place-name truncations: { felt: "feltham", ashfrd: "ashford" }).
 */
function withAliases(aliases = {}) {
  const normalized = {};
  for (const [key, value] of Object.entries(aliases)) {
    normalized[String(key).toLowerCase()] = String(value).toLowerCase();
  }
  return { ...COMMON_TYPOS, ...normalized };
}

module.exports = {
  stripPunctuation,
  correctToken,
  correctTypos,
  normalizeAddress,
  normalizeAddressLines,
  addressesMatch,
  withAliases,
  COMMON_TYPOS,
};
