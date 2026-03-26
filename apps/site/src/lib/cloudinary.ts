const CLOUD_NAME = import.meta.env.PUBLIC_CLOUDINARY_CLOUD_NAME;

type ImageOptions = {
  width?: number;
  quality?: number;
  format?: "auto" | "webp" | "jpg" | "png";
};

export function cloudinaryUrl(
  publicId: string,
  { width = 1200, quality = 80, format = "auto" }: ImageOptions = {}
): string {
  if (!CLOUD_NAME) {
    console.warn("PUBLIC_CLOUDINARY_CLOUD_NAME is not set");
    return "";
  }
  const transforms = [`w_${width}`, `q_${quality}`, `f_${format}`].join(",");
  return `https://res.cloudinary.com/${CLOUD_NAME}/image/upload/${transforms}/${publicId}`;
}

export function cloudinaryOgUrl(publicId: string): string {
  return cloudinaryUrl(publicId, { width: 1200, quality: 90, format: "jpg" });
}
