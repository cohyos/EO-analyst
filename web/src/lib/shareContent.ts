import DOMPurify from "dompurify";

export interface ShareLink {
  url?: string | null;
  title?: string | null;
  n?: number;
}
export interface ShareContent {
  title: string;
  text: string;
  html: string;
}

export function externalUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!/^https?:$/.test(url.protocol)) return null;
    const host = url.hostname.toLowerCase();
    if (
      host === "localhost" ||
      host.endsWith(".localhost") ||
      host === "[::1]" ||
      /^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host)
    )
      return null;
    return url.href;
  } catch {
    return null;
  }
}

/** Export content, never the application's controls or private localhost navigation. */
export function prepareShareContent(
  element: HTMLElement,
  title?: string,
  links: ShareLink[] = [],
): ShareContent {
  const body = element.cloneNode(true) as HTMLElement;
  body.querySelectorAll('button[aria-describedby^="citation-"]').forEach((button) => {
    const n = Number(button.textContent?.trim());
    const source = links.find((link) => link.n === n);
    const url = externalUrl(source?.url);
    const replacement = document.createElement(url ? "a" : "span");
    replacement.textContent = `[${n}]`;
    if (url) replacement.setAttribute("href", url);
    button.replaceWith(replacement);
  });
  body
    .querySelectorAll(
      "[data-share-actions], [data-share-exclude], nav, button, input, textarea, select, script, style, iframe, [role=tooltip], [role=tablist]",
    )
    .forEach((node) => node.remove());
  const sources = new Map<string, string>();
  for (const link of links) {
    const url = externalUrl(link.url);
    if (url)
      sources.set(url, `${link.n != null ? `[${link.n}] ` : ""}${link.title || url}`);
  }
  body.querySelectorAll("a").forEach((anchor) => {
    const url =
      externalUrl(anchor.getAttribute("data-url")) ||
      externalUrl(anchor.getAttribute("href"));
    if (url) {
      anchor.setAttribute("href", url);
      if (!sources.has(url)) sources.set(url, anchor.textContent?.trim() || url);
    } else anchor.replaceWith(...Array.from(anchor.childNodes));
  });
  const resolvedTitle =
    title || body.querySelector("h1,h2,h3")?.textContent?.trim() || "EO-Analyst";
  if (sources.size) {
    const appendix = document.createElement("section");
    for (const [url, label] of sources) {
      const p = document.createElement("p");
      const a = document.createElement("a");
      a.href = url;
      a.textContent = label === url ? url : `${label}: ${url}`;
      p.append(a);
      appendix.append(p);
    }
    body.append(appendix);
  }
  const html = DOMPurify.sanitize(body.innerHTML, {
    ALLOWED_TAGS: [
      "p",
      "div",
      "section",
      "article",
      "h1",
      "h2",
      "h3",
      "h4",
      "h5",
      "ul",
      "ol",
      "li",
      "table",
      "thead",
      "tbody",
      "tr",
      "th",
      "td",
      "a",
      "strong",
      "b",
      "em",
      "i",
      "br",
      "blockquote",
      "bdi",
      "span",
      "hr",
    ],
    ALLOWED_ATTR: ["href", "dir", "colspan", "rowspan"],
  });
  const plain = document.createElement("div");
  plain.innerHTML = html;
  plain.querySelectorAll("br").forEach((node) => node.replaceWith("\n"));
  plain.querySelectorAll("th,td").forEach((node) => node.append("\t"));
  plain
    .querySelectorAll("p,div,section,article,h1,h2,h3,h4,h5,li,tr,blockquote,hr")
    .forEach((node) => node.append("\n"));
  const text = (plain.textContent || "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return {
    title: resolvedTitle,
    text: `${resolvedTitle}\n\n${text}`,
    html: `<div dir="rtl">${html}</div>`,
  };
}

export function composeShareUrl(channel: "email" | "whatsapp", content: ShareContent) {
  const full =
    channel === "email"
      ? `mailto:?subject=${encodeURIComponent(content.title)}&body=${encodeURIComponent(content.text)}`
      : `https://wa.me/?text=${encodeURIComponent(content.text)}`;
  // Long reports travel through the clipboard instead of being silently truncated by URL handlers.
  const needsPaste = full.length > 7000;
  return {
    needsPaste,
    url: !needsPaste
      ? full
      : channel === "email"
        ? `mailto:?subject=${encodeURIComponent(content.title)}`
        : "https://wa.me/",
  };
}

export async function copyShareContent(content: ShareContent): Promise<void> {
  if (navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([content.html], { type: "text/html" }),
          "text/plain": new Blob([content.text], { type: "text/plain" }),
        }),
      ]);
      return;
    } catch {
      /* Try plain text if rich clipboard formats are unsupported. */
    }
  }
  if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
  await navigator.clipboard.writeText(content.text);
}
