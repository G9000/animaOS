interface Box {
  type: string;
  offset: number;
  size: number;
  /** data start */
  ds: number;
  /** data end */
  de: number;
}

function readString(dv: DataView, offset: number, n: number): string {
  let s = "";
  for (let i = 0; i < n; i++) s += String.fromCharCode(dv.getUint8(offset + i));
  return s;
}

const CONTAINER_BOXES = new Set([
  "moov","trak","mdia","minf","stbl","edts","moof","traf","mvex","dinf","sinf","schi","udta",
]);

function parseBoxes(buf: ArrayBuffer, start: number, end: number): Box[] {
  const dv = new DataView(buf);
  const result: Box[] = [];
  let offset = start;

  while (offset + 8 <= end) {
    let size = dv.getUint32(offset);
    const type = readString(dv, offset + 4, 4);
    let headerSize = 8;

    if (size === 1) { size = Number(dv.getBigUint64(offset + 8)); headerSize = 16; }
    if (size === 0) size = end - offset;
    if (size < 8 || offset + size > end) break;

    result.push({ type, offset, size, ds: offset + headerSize, de: offset + size });
    offset += size;
  }
  return result;
}

function findBox(buf: ArrayBuffer, start: number, end: number, path: string): Box | null {
  const parts = path.split("/");
  let boxes = parseBoxes(buf, start, end);

  for (let i = 0; i < parts.length; i++) {
    const found = boxes.find(x => x.type === parts[i]);
    if (!found) return null;
    if (i === parts.length - 1) return found;
    const dataStart = found.ds + (CONTAINER_BOXES.has(found.type) ? 0 : found.type === "stsd" ? 8 : 0);
    boxes = parseBoxes(buf, dataStart, found.de);
  }
  return null;
}

function findAllBoxes(buf: ArrayBuffer, start: number, end: number, type: string): Box[] {
  const result: Box[] = [];
  function walk(s: number, e: number) {
    for (const box of parseBoxes(buf, s, e)) {
      if (box.type === type) result.push(box);
      if (CONTAINER_BOXES.has(box.type)) walk(box.ds, box.de);
    }
  }
  walk(start, end);
  return result;
}

export interface Sample {
  offset: number;
  size: number;
  dts: number;
  isKey: boolean;
  timestampUs: number;
  durationUs: number;
}

export interface DemuxResult {
  codec: string;
  desc: ArrayBuffer | null;
  samples: Sample[];
  duration: number;
  buf: ArrayBuffer;
}

export function demux(buf: ArrayBuffer): DemuxResult {
  const dv = new DataView(buf);
  const tracks = findAllBoxes(buf, 0, buf.byteLength, "trak");

  let videoTrack: Box | null = null;
  for (const track of tracks) {
    const hdlr = findBox(buf, track.ds, track.de, "mdia/hdlr");
    if (hdlr && readString(dv, hdlr.ds + 8, 4) === "vide") { videoTrack = track; break; }
  }
  if (!videoTrack) throw new Error("No video track");

  const mdhd = findBox(buf, videoTrack.ds, videoTrack.de, "mdia/mdhd");
  let timeScale = 1000;
  if (mdhd) {
    const version = dv.getUint8(mdhd.ds);
    timeScale = version === 0 ? dv.getUint32(mdhd.ds + 12) : dv.getUint32(mdhd.ds + 20);
  }

  const stbl = findBox(buf, videoTrack.ds, videoTrack.de, "mdia/minf/stbl");
  if (!stbl) throw new Error("No sample table");

  const stsd = findBox(buf, stbl.ds, stbl.de, "stsd");
  let codec = "avc1.42001E";
  let desc: ArrayBuffer | null = null;

  if (stsd) {
    const es = stsd.ds + 8;
    const et = readString(dv, es + 4, 4);
    if (et === "avc1" || et === "avc3") {
      const inner = parseBoxes(buf, es + 86, stsd.de);
      const avcC = inner.find(b => b.type === "avcC");
      if (avcC) {
        const d = avcC.ds;
        codec = `avc1.${dv.getUint8(d+1).toString(16).padStart(2,"0")}${dv.getUint8(d+2).toString(16).padStart(2,"0")}${dv.getUint8(d+3).toString(16).padStart(2,"0")}`;
        desc = buf.slice(avcC.ds, avcC.de);
      }
    } else if (et === "hev1" || et === "hvc1") {
      const inner = parseBoxes(buf, es + 86, stsd.de);
      const hvcC = inner.find(b => b.type === "hvcC");
      if (hvcC) { desc = buf.slice(hvcC.ds, hvcC.de); codec = "hev1.1.6.L93.B0"; }
    }
  }

  const sttsBox = findBox(buf, stbl.ds, stbl.de, "stts");
  const sttsEntries: Array<{ count: number; delta: number }> = [];
  if (sttsBox) {
    const count = dv.getUint32(sttsBox.ds + 4);
    let o = sttsBox.ds + 8;
    for (let i = 0; i < count; i++) { sttsEntries.push({ count: dv.getUint32(o), delta: dv.getUint32(o + 4) }); o += 8; }
  }

  const cttsBox = findBox(buf, stbl.ds, stbl.de, "ctts");
  const cttsEntries: Array<{ count: number; offset: number }> = [];
  if (cttsBox) {
    const version = dv.getUint8(cttsBox.ds);
    const count = dv.getUint32(cttsBox.ds + 4);
    let o = cttsBox.ds + 8;
    for (let i = 0; i < count; i++) {
      cttsEntries.push({ count: dv.getUint32(o), offset: version === 0 ? dv.getUint32(o + 4) : dv.getInt32(o + 4) });
      o += 8;
    }
  }

  const stszBox = findBox(buf, stbl.ds, stbl.de, "stsz");
  const sizes: number[] = [];
  if (stszBox) {
    const defaultSize = dv.getUint32(stszBox.ds + 4);
    const count = dv.getUint32(stszBox.ds + 8);
    let o = stszBox.ds + 12;
    for (let i = 0; i < count; i++) { sizes.push(defaultSize || dv.getUint32(o)); if (!defaultSize) o += 4; }
  }

  const chunkOffsets: number[] = [];
  const stco = findBox(buf, stbl.ds, stbl.de, "stco");
  const co64 = findBox(buf, stbl.ds, stbl.de, "co64");
  if (stco) {
    const count = dv.getUint32(stco.ds + 4); let o = stco.ds + 8;
    for (let i = 0; i < count; i++) { chunkOffsets.push(dv.getUint32(o)); o += 4; }
  } else if (co64) {
    const count = dv.getUint32(co64.ds + 4); let o = co64.ds + 8;
    for (let i = 0; i < count; i++) { chunkOffsets.push(Number(dv.getBigUint64(o))); o += 8; }
  }

  const stscBox = findBox(buf, stbl.ds, stbl.de, "stsc");
  const stscEntries: Array<{ fc: number; spc: number }> = [];
  if (stscBox) {
    const count = dv.getUint32(stscBox.ds + 4); let o = stscBox.ds + 8;
    for (let i = 0; i < count; i++) { stscEntries.push({ fc: dv.getUint32(o), spc: dv.getUint32(o + 4) }); o += 12; }
  }

  const stssBox = findBox(buf, stbl.ds, stbl.de, "stss");
  const syncSet = new Set<number>();
  if (stssBox) {
    const count = dv.getUint32(stssBox.ds + 4); let o = stssBox.ds + 8;
    for (let i = 0; i < count; i++) { syncSet.add(dv.getUint32(o)); o += 4; }
  }

  // build sample offset table from chunk map
  const sampleOffsets: number[] = [];
  let si = 0;
  for (let ci = 0; ci < chunkOffsets.length; ci++) {
    let spc = 1;
    for (let e = stscEntries.length - 1; e >= 0; e--) {
      if (ci + 1 >= stscEntries[e].fc) { spc = stscEntries[e].spc; break; }
    }
    let off = chunkOffsets[ci];
    for (let s = 0; s < spc && si < sizes.length; s++) { sampleOffsets.push(off); off += sizes[si]; si++; }
  }

  const dtsArray: number[] = [];
  let dts = 0; si = 0;
  for (const entry of sttsEntries) {
    for (let i = 0; i < entry.count && si < sizes.length; i++) { dtsArray.push(dts); dts += entry.delta; si++; }
  }

  const ctsArray = new Array<number>(sizes.length).fill(0);
  if (cttsEntries.length) {
    let idx = 0;
    for (const entry of cttsEntries) {
      for (let i = 0; i < entry.count && idx < ctsArray.length; i++) ctsArray[idx++] = entry.offset;
    }
  }

  const allKey = syncSet.size === 0;
  const samples: Sample[] = sizes.map((sz, i) => ({
    offset: sampleOffsets[i],
    size: sz,
    dts: dtsArray[i] ?? 0,
    isKey: allKey || syncSet.has(i + 1),
    timestampUs: Math.round(((dtsArray[i] ?? 0) + ctsArray[i]) / timeScale * 1e6),
    durationUs: Math.round((sttsEntries[0]?.delta ?? 1) / timeScale * 1e6),
  }));

  samples.sort((a, b) => a.dts - b.dts);
  return { codec, desc, samples, duration: dts / timeScale, buf };
}
