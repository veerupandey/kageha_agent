import { describe, expect, it } from "vitest";
import { plainSpeakable } from "./voiceClient";

describe("plainSpeakable", () => {
  it("strips markdown for TTS", () => {
    expect(
      plainSpeakable("## Hello\n\nUse `gemini_tts` and [docs](https://x.test)"),
    ).toBe("Hello Use gemini_tts and docs");
  });

  it("drops fenced code", () => {
    expect(plainSpeakable("Hi\n```js\nconsole.log(1)\n```\nBye")).toBe(
      "Hi Bye",
    );
  });
});
