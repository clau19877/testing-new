const { COMMON_TYPOS } = require("./typos");
const {
  stripPunctuation,
  correctTypos,
  normalizeAddress,
  normalizeAddressLines,
  addressesMatch,
  withAliases,
} = require("./normalize");

module.exports = {
  COMMON_TYPOS,
  stripPunctuation,
  correctTypos,
  normalizeAddress,
  normalizeAddressLines,
  addressesMatch,
  withAliases,
};
