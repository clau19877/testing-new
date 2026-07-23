const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const {
  stripPunctuation,
  correctTypos,
  normalizeAddress,
  normalizeAddressLines,
  addressesMatch,
  withAliases,
} = require("./normalize");

describe("stripPunctuation", () => {
  it("removes dots, dashes, underscores, and commas", () => {
    assert.equal(
      stripPunctuation("Un.it.13_Demo-Centr. 100--Main-Road,,"),
      "Unit 13 Demo Centr 100 Main Road"
    );
  });

  it("rejoins letters split by inserted dots or underscores", () => {
    assert.equal(stripPunctuation("Bus.i.ness"), "Business");
    assert.equal(stripPunctuation("Fel_tham"), "Feltham");
  });

  it("collapses mixed whitespace", () => {
    assert.equal(stripPunctuation("  unit   5\tHall  "), "unit 5 Hall");
  });
});

describe("correctTypos", () => {
  it("fixes common centre / business / road typos", () => {
    assert.equal(
      correctTypos("busines centr raod"),
      "business centre road"
    );
  });

  it("expands abbreviations", () => {
    assert.equal(correctTypos("12 High St"), "12 high street");
    assert.equal(correctTypos("4 Park Ave"), "4 park avenue");
    assert.equal(correctTypos("9 Mill Ln"), "9 mill lane");
  });

  it("splits glued unit numbers", () => {
    assert.equal(correctTypos("unit13"), "unit 13");
    assert.equal(correctTypos("uint7"), "unit 7");
  });
});

describe("normalizeAddress", () => {
  it("returns empty string for blank input", () => {
    assert.equal(normalizeAddress(""), "");
    assert.equal(normalizeAddress("   "), "");
    assert.equal(normalizeAddress(null), "");
  });

  it("produces a lowercase canonical form", () => {
    assert.equal(
      normalizeAddress("Unit 4 Riverside Business Centre, 12 Oak Road"),
      "unit 4 riverside business centre 12 oak road"
    );
  });

  it("normalizes noisy punctuation + typos into the same form", () => {
    const clean =
      "unit 13 demo business centre 166 sample road";
    const noisy =
      "Un.it.13_Demo-Busines-Centr. 166--Sampel-Raod,,";

    // "sampel" is not in the default dictionary — extend for this case
    const withExtra = {
      ...require("./typos").COMMON_TYPOS,
      sampel: "sample",
    };

    assert.equal(
      normalizeAddress(noisy, { dictionary: withExtra }),
      clean
    );
  });

  it("supports uppercase output", () => {
    assert.equal(
      normalizeAddress("unit 1 hall rd", { uppercase: true }),
      "UNIT 1 HALL ROAD"
    );
  });
});

describe("normalizeAddressLines", () => {
  it("joins multi-line profiles", () => {
    assert.equal(
      normalizeAddressLines([
        "Unit 2 North Business Centre",
        "88 Bridge Road",
      ]),
      "unit 2 north business centre 88 bridge road"
    );
  });

  it("normalizes a clean multi-line profile", () => {
    assert.equal(
      normalizeAddressLines([
        "Unit 13 Ashford Business Centre",
        "166 Feltham Road",
      ]),
      "unit 13 ashford business centre 166 feltham road"
    );
  });

  it("needs place aliases when noisy input truncates place names", () => {
    const noisy = "Un.it.13_Ashford-Centr. 166--Felt-Road,,";
    const dictionary = withAliases({ felt: "feltham" });

    // "Business" was dropped in the noisy form; place truncation is aliased.
    assert.equal(
      normalizeAddressLines(noisy, { dictionary }),
      "unit 13 ashford centre 166 feltham road"
    );
  });
});

describe("addressesMatch", () => {
  it("returns true when punctuation/case differ but content matches", () => {
    assert.equal(
      addressesMatch(
        "Unit 5 Maple Business Centre\n10 River Road",
        "unit-5_maple-business-centre, 10 river rd"
      ),
      true
    );
  });

  it("returns false when street numbers differ", () => {
    assert.equal(
      addressesMatch("1 High Street", "2 High Street"),
      false
    );
  });
});
