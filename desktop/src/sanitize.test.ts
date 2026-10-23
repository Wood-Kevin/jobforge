// P1.8 acceptance: a posting with <script> / onerror renders inert. The sanitized HTML is put into a
// real DOM (jsdom) the way the job view does (innerHTML), and nothing may run or survive.
import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";
import { decodeIfEscaped, makeSanitizer } from "./sanitize";

function render(html: string) {
  const dom = new JSDOM("<!doctype html><body><div id=root></div></body>", { runScripts: "dangerously" });
  const win = dom.window as unknown as Window & typeof globalThis & { __xss?: number };
  const out = makeSanitizer(win)(html);
  win.document.getElementById("root")!.innerHTML = out;
  return { win, out, root: win.document.getElementById("root")! };
}

const HOSTILE = [
  `<p>Hi</p><script>window.__xss = 1</script>`,
  `<img src=x onerror="window.__xss = 2">`,
  `<p onclick="window.__xss = 3" onmouseover="window.__xss = 3">hover</p>`,
  `<a href="javascript:window.__xss = 4">click</a>`,
  `<svg onload="window.__xss = 5"><circle/></svg>`,
  `<iframe src="javascript:window.__xss = 6"></iframe>`,
  `<style>body{background:url(https://tracker.example/p.gif)}</style>`,
  `<form action="https://evil.example"><input name=pw></form>`,
  `<a href="https://ok.example" target="_blank" style="color:red">ok</a>`,
  // Greenhouse escapes its HTML; an escaped script must not come back to life after decoding.
  `&lt;p&gt;Hi&lt;/p&gt;&lt;script&gt;window.__xss = 7&lt;/script&gt;&lt;img src=x onerror="window.__xss = 8"&gt;`,
  `<math><mi xlink:href="javascript:window.__xss=9">x</mi></math>`,
];

describe("posting sanitizer", () => {
  it.each(HOSTILE)("renders %s inert", (html) => {
    const { win, out, root } = render(html);
    expect(win.__xss).toBeUndefined();
    expect(out).not.toMatch(/<script|<img|<svg|<iframe|<style|<form|<input|<math|javascript:/i);
    for (const el of Array.from(root.querySelectorAll("*"))) {
      for (const attr of Array.from(el.attributes)) {
        expect(attr.name).not.toMatch(/^on|^style$|^target$|^src$|^action$/);
      }
    }
  });

  it("keeps formatting and safe links", () => {
    const { out, root } = render(`<h2>About</h2><p style="min-height:1.5em">We <strong>build</strong> <a href="https://acme.example/jobs">things</a>.</p><ul><li>Linux</li></ul>`);
    expect(out).toContain("<h2>About</h2>");
    expect(out).toContain("<strong>build</strong>");
    const a = root.querySelector("a")!;
    expect(a.getAttribute("href")).toBe("https://acme.example/jobs");
    expect(a.getAttribute("rel")).toBe("noreferrer noopener");
    expect(root.querySelector("p")!.hasAttribute("style")).toBe(false);
    expect(root.querySelector("li")!.textContent).toBe("Linux");
  });

  it("decodes Greenhouse's escaped HTML once, and only when it is escaped", () => {
    const win = new JSDOM("").window as unknown as Window & typeof globalThis;
    expect(decodeIfEscaped("&lt;h2&gt;&lt;strong&gt;About&lt;/strong&gt;&lt;/h2&gt;", win)).toBe("<h2><strong>About</strong></h2>");
    expect(decodeIfEscaped("<p>5 &lt; 6</p>", win)).toBe("<p>5 &lt; 6</p>"); // real markup: untouched
    expect(decodeIfEscaped("Plain text, no tags", win)).toBe("Plain text, no tags");
  });
});
