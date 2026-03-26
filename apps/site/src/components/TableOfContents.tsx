import { useEffect, useState } from "react";

interface Heading {
  id: string;
  text: string;
  el: Element;
}

function slugify(text: string) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function collectHeadings(article: Element): Heading[] {
  const results: Heading[] = [];

  // Real markdown headings (## h2)
  article.querySelectorAll("h2").forEach((el) => {
    if (!el.id) el.id = slugify(el.textContent ?? "");
    if (el.id) results.push({ id: el.id, text: el.textContent ?? "", el });
  });

  // Bold-only paragraphs used as section titles: <p><strong>...</strong></p>
  if (results.length === 0) {
    article.querySelectorAll("p").forEach((p) => {
      const children = Array.from(p.childNodes).filter(
        (n) => !(n.nodeType === Node.TEXT_NODE && n.textContent?.trim() === "")
      );
      if (children.length === 1 && (children[0] as Element).tagName === "STRONG") {
        const text = (children[0] as Element).textContent ?? "";
        if (!text) return;
        const id = slugify(text);
        p.id = id;
        results.push({ id, text, el: p });
      }
    });
  }

  return results;
}

export default function TableOfContents() {
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [activeId, setActiveId] = useState<string>("");

  useEffect(() => {
    const article = document.querySelector("article");
    if (!article) return;
    setHeadings(collectHeadings(article));
  }, []);

  useEffect(() => {
    if (headings.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveId(entry.target.id);
            break;
          }
        }
      },
      { rootMargin: "-15% 0% -75% 0%" }
    );

    headings.forEach(({ el }) => observer.observe(el));
    return () => observer.disconnect();
  }, [headings]);

  if (headings.length === 0) return null;

  return (
    <nav>
      <p className="font-mono text-[8px] tracking-[0.3em] uppercase text-muted-foreground/25 mb-5">
        // contents
      </p>
      <ul className="space-y-1">
        {headings.map(({ id, text }) => (
          <li key={id}>
            <a
              href={`#${id}`}
              className={`group relative overflow-hidden block font-mono text-[9px] tracking-[0.04em] leading-relaxed px-2 py-1 transition-colors duration-150
                before:absolute before:inset-0 before:-translate-x-full hover:before:translate-x-0 before:transition-transform before:duration-500 before:ease-[cubic-bezier(0.16,1,0.3,1)] before:bg-foreground
                ${activeId === id
                  ? "text-background before:translate-x-0"
                  : "text-muted-foreground/50 hover:text-background"
                }`}
            >
              <span className="relative z-10">{text}</span>
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
