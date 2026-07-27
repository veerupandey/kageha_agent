import { describe, expect, it } from "vitest";
import { getAtContext, getSlashContext } from "./slash";
import {
  isComputerAdminSlash,
  isMetaOnlySlash,
  isModeOnlyComposerText,
} from "../store/helpers";

describe("getSlashContext", () => {
  it("detects slash token at caret", () => {
    const text = "hello /pl";
    const ctx = getSlashContext(text, text.length);
    expect(ctx).toEqual({
      start: 6,
      end: 9,
      token: "/pl",
      query: "pl",
    });
  });

  it("returns null when not in a slash token", () => {
    expect(getSlashContext("hello world", 5)).toBeNull();
    expect(getSlashContext("email@x.com", 11)).toBeNull();
  });

  it("allows slash at start of input", () => {
    const ctx = getSlashContext("/plan", 5);
    expect(ctx?.token).toBe("/plan");
    expect(ctx?.query).toBe("plan");
    expect(ctx?.start).toBe(0);
  });
});

describe("getAtContext", () => {
  it("detects @token at caret", () => {
    const text = "see @file";
    const ctx = getAtContext(text, text.length);
    expect(ctx).toEqual({
      start: 4,
      end: 9,
      token: "@file",
      query: "file",
    });
  });

  it("returns null outside @token", () => {
    expect(getAtContext("plain text", 4)).toBeNull();
  });
});

describe("meta-only slash (must not mint sessions)", () => {
  it("treats mode-only and bare skill prefixes as meta", () => {
    expect(isModeOnlyComposerText("/plan")).toBe(true);
    expect(isMetaOnlySlash("/plan")).toBe(true);
    expect(isMetaOnlySlash("/computer")).toBe(true);
    expect(isMetaOnlySlash("/computer_use")).toBe(true);
    expect(isComputerAdminSlash("/computer status")).toBe(true);
    expect(isMetaOnlySlash("/computer status")).toBe(true);
  });

  it("lets skill tasks through to the agent", () => {
    expect(isComputerAdminSlash("/computer open Calculator")).toBe(false);
    expect(isMetaOnlySlash("/computer open Calculator")).toBe(false);
    expect(isMetaOnlySlash("/computer_use open Chrome")).toBe(false);
  });
});
