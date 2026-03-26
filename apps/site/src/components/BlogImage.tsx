interface Props {
  src: string;
  alt: string;
  caption?: string;
  credit?: string;
}

export default function BlogImage({ src, alt, caption, credit }: Props) {
  return (
    <figure className="my-10 -mx-6">
      <img src={src} alt={alt} className="w-full object-cover" />
      {(caption || credit) && (
        <figcaption className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/30 mt-3 px-6">
          {caption}
          {caption && credit && " — "}
          {credit}
        </figcaption>
      )}
    </figure>
  );
}
