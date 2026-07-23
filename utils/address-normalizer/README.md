# Address normalizer

Strips punctuation / separator noise and corrects common address typos so noisy inputs can be compared or stored in a canonical form.

## Usage

```js
const {
  normalizeAddress,
  normalizeAddressLines,
  addressesMatch,
  withAliases,
} = require("./utils/address-normalizer");

normalizeAddress("Un.it.4_Riverside-Centr. 12--Oak-Raod,,");
// => "unit 4 riverside centre 12 oak road"

const dictionary = withAliases({
  felt: "feltham", // known place truncations / aliases
});

normalizeAddressLines(
  ["Unit 13 Ashford Business Centre", "166 Feltham Road"],
  { dictionary }
);
// => "unit 13 ashford business centre 166 feltham road"

addressesMatch(
  "Unit 5 Maple Business Centre\n10 River Road",
  "unit-5_maple-business-centre, 10 river rd"
);
// => true
```

## What it does

1. Unicode-normalizes input (`NFKC`)
2. Replaces non-letter/number runs with spaces (dots, dashes, underscores, commas, etc.)
3. Lowercases tokens
4. Corrects common typos / abbreviations (`raod` → `road`, `centr` → `centre`, `st` → `street`, …)
5. Splits glued unit forms (`unit13` → `unit 13`)

Extend `withAliases({ ... })` for site-specific place names that get truncated in noisy input.

## Test

```sh
npm test
```
