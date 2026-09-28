import { describe, expect, it } from "vitest";
import { PANEL_BG, TONE, tokenize } from "./JsonViewer";

/** WCAG 2.1 relative luminance. */
const luminance = (hex: string) => {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

describe("token colours", () => {
  const hexOf = (cls: string) => cls.match(/\[(#[0-9A-Fa-f]{6})\]/)?.[1] ?? "";

  it.each(Object.entries(TONE))(
    "%s clears WCAG AA against the panel background",
    (_name, cls) => {
      expect(contrast(hexOf(cls), PANEL_BG)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it("keeps every token a distinct colour", () => {
    const hexes = Object.values(TONE).map(hexOf);

    expect(new Set(hexes).size).toBe(hexes.length);
  });
});

/** Round-trip the pieces so we assert on structure, not on span identity. */
const flat = (json: string) => tokenize(json).map((p) => p.text).join("");
const toneOf = (json: string, needle: string) =>
  tokenize(json).find((p) => p.text === needle)?.tone;

describe("JSON tokenizer", () => {
  it("never loses or reorders a single character", () => {
    const json = JSON.stringify(
      {
        class_uid: 2002,
        activity_name: "UserAction",
        severity: "high",
        confidence: 0.9,
        nested: { ok: true, missing: null, list: [1, -2, 3.5] },
        text: 'he said "hi"',
      },
      null,
      2,
    );

    expect(flat(json)).toBe(json);
  });

  it("marks a string as a key only when a colon follows it", () => {
    const json = '{"user":"erin","nested":{"id":7}}';

    expect(toneOf(json, '"user"')).toBe("text-[#0B7A3E] font-semibold");
    expect(toneOf(json, '"erin"')).toBe("text-[#C2185B]");
    expect(toneOf(json, '"nested"')).toBe("text-[#0B7A3E] font-semibold");
    expect(toneOf(json, '"id"')).toBe("text-[#0B7A3E] font-semibold");
  });

  it("does not read a number or keyword inside a string as a token", () => {
    const json = '{"msg":"count 42 and true and null"}';

    expect(toneOf(json, '"count 42 and true and null"')).toBe(
      "text-[#C2185B]",
    );
    expect(tokenize(json).some((p) => p.text === "42")).toBe(false);
    expect(tokenize(json).some((p) => p.text === "true")).toBe(false);
  });

  it("handles escaped quotes and backslashes inside a value", () => {
    const json = JSON.stringify({ path: 'C:\\logs\\"a".txt' }, null, 2);

    expect(flat(json)).toBe(json);
    expect(toneOf(json, '"C:\\\\logs\\\\\\"a\\".txt"')).toBe("text-[#C2185B]");
  });

  it("colours numbers, booleans and null apart from each other", () => {
    const json = '{"n":-12,"f":false,"z":null,"e":1.5e3}';

    expect(toneOf(json, "-12")).toBe("text-[#B54708]");
    expect(toneOf(json, "1.5e3")).toBe("text-[#B54708]");
    expect(toneOf(json, "false")).toBe("text-[#1a56db] font-semibold");
    expect(toneOf(json, "null")).toBe("text-[#B91C1C] font-semibold");
  });

  it("emits no zero-length spans, and keeps bare braces muted", () => {
    for (const json of ["{}", "[]", "", '{"a":1}']) {
      expect(tokenize(json).every((p) => p.text.length > 0)).toBe(true);
      expect(flat(json)).toBe(json);
    }
    expect(tokenize("{}")).toEqual([{ text: "{}", tone: "text-[#5B6B85]" }]);
  });
});
