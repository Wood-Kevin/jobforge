// Job descriptions are untrusted HTML from employers (ARCHITECTURE 8). They are rendered only
// after this sanitizer: a fixed allow-list of formatting tags, no scripts, no event handlers, no
// styles, no images (no tracking pixels), and only http(s) links, which the window opens in the
// system browser through the host's open_url command, never inside the webview.
import createDOMPurify from "dompurify";

// The browser window, or jsdom's in tests: both carry DOMParser and the DOM constructors.
type Win = Window & typeof globalThis;

const ALLOWED_TAGS = ["p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u", "h1", "h2", "h3", "h4", "h5", "h6",
  "a", "span", "div", "blockquote", "table", "thead", "tbody", "tr", "td", "th", "hr", "code", "pre", "sup", "sub"];
const ALLOWED_ATTR = ["href", "title"];
const SAFE_URL = /^https?:\/\//i;

// Greenhouse sends its HTML entity-escaped ("&lt;p&gt;"); Lever and Ashby send markup. Decode once
// when the text holds escaped tags and no real ones. Decoding happens BEFORE sanitizing, so an
// escaped "&lt;script&gt;" becomes a real tag that the sanitizer then removes.
export function decodeIfEscaped(raw: string, win: Win): string {
  if (/<[a-z][\s\S]*>/i.test(raw) || !/&lt;\/?[a-z]/i.test(raw)) return raw;
  const doc = new win.DOMParser().parseFromString(`<!doctype html><body>${raw}`, "text/html");
  return doc.body.textContent ?? "";
}

export function makeSanitizer(win: Win) {
  const purify = createDOMPurify(win);
  purify.addHook("afterSanitizeAttributes", (node) => {
    if (node.nodeName === "A") {
      const href = node.getAttribute("href") ?? "";
      if (!SAFE_URL.test(href)) node.removeAttribute("href");
      node.removeAttribute("target");
      node.setAttribute("rel", "noreferrer noopener");
    }
  });
  return (raw: string | null | undefined): string => {
    if (!raw) return "";
    return purify.sanitize(decodeIfEscaped(raw, win), {
      ALLOWED_TAGS, ALLOWED_ATTR, ALLOWED_URI_REGEXP: SAFE_URL, ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false,
      FORBID_TAGS: ["style", "script", "iframe", "object", "embed", "form", "input", "img", "svg", "math", "link", "meta", "base"],
    }) as string;
  };
}

let defaultSanitizer: ((raw: string | null | undefined) => string) | null = null;

export function sanitizePosting(raw: string | null | undefined): string {
  defaultSanitizer ??= makeSanitizer(window);
  return defaultSanitizer(raw);
}
