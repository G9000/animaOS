export function sobelEdge(gray: Uint8Array, w: number, h: number): Uint8Array {
  const out = new Uint8Array(w * h);
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const tl = gray[(y-1)*w + x-1], tc = gray[(y-1)*w + x], tr = gray[(y-1)*w + x+1];
      const ml = gray[y*w + x-1],                               mr = gray[y*w + x+1];
      const bl = gray[(y+1)*w + x-1], bc = gray[(y+1)*w + x], br = gray[(y+1)*w + x+1];
      const gx = -tl - 2*ml - bl + tr + 2*mr + br;
      const gy = -tl - 2*tc - tr + bl + 2*bc + br;
      out[y*w + x] = Math.min(255, Math.sqrt(gx*gx + gy*gy) | 0);
    }
  }
  return out;
}
