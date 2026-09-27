// QR codes, fully offline. Wraps the vendored qrcode-generator (MIT, see vendor/LICENSE-qrcode-generator.txt).
// A QR code must be dark modules on white to scan, in any theme, so the exported files use
// literal black and white; the on-screen copy is coloured by the --qr-* tokens in theme.css.
import qrcode from '../vendor/qrcode-generator-2.0.4.mjs';

const NS = 'http://www.w3.org/2000/svg';
export const QUIET = 4; // modules of white border the spec requires around the code

// qrMatrix(text) -> boolean[][] (true = dark), error correction level M, smallest version that fits.
// Text is encoded as UTF-8 bytes.
export function qrMatrix(text) {
  const qr = qrcode(0, 'M');
  const bytes = new TextEncoder().encode(String(text));
  qr.addData(String.fromCharCode(...bytes), 'Byte');
  qr.make();
  const n = qr.getModuleCount();
  return Array.from({ length: n }, (_, r) => Array.from({ length: n }, (_, c) => qr.isDark(r, c)));
}

// One path of 1x1 squares for the dark modules, offset by the quiet zone.
function darkPath(m) {
  let d = '';
  for (let r = 0; r < m.length; r++) {
    for (let c = 0; c < m.length; c++) if (m[r][c]) d += `M${c + QUIET} ${r + QUIET}h1v1h-1z`;
  }
  return d;
}

// Crisp SVG element (for the page).
export function qrSvg(m, { label = null } = {}) {
  const size = m.length + QUIET * 2;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
  svg.setAttribute('shape-rendering', 'crispEdges');
  svg.setAttribute('class', 'qr');
  if (label) { svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', label); }
  else svg.setAttribute('aria-hidden', 'true');
  const bg = document.createElementNS(NS, 'rect');
  bg.setAttribute('class', 'qr-bg');
  bg.setAttribute('width', String(size));
  bg.setAttribute('height', String(size));
  bg.setAttribute('fill', '#ffffff');
  const p = document.createElementNS(NS, 'path');
  p.setAttribute('class', 'qr-fg');
  p.setAttribute('d', darkPath(m));
  p.setAttribute('fill', '#000000');
  svg.append(bg, p);
  return svg;
}

// Standalone SVG file.
export function qrSvgBlob(m) {
  const size = m.length + QUIET * 2;
  const px = size * 16;
  const text = `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="${NS}" viewBox="0 0 ${size} ${size}" width="${px}" height="${px}" shape-rendering="crispEdges">`
    + `<rect width="${size}" height="${size}" fill="#ffffff"/><path d="${darkPath(m)}" fill="#000000"/></svg>\n`;
  return new Blob([text], { type: 'image/svg+xml' });
}

// PNG at least `min` pixels square, a whole number of pixels per module so edges stay sharp.
export function qrPngBlob(m, min = 1024) {
  const size = m.length + QUIET * 2;
  const scale = Math.ceil(min / size);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size * scale;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#000000';
  for (let r = 0; r < m.length; r++) {
    for (let c = 0; c < m.length; c++) if (m[r][c]) ctx.fillRect((c + QUIET) * scale, (r + QUIET) * scale, scale, scale);
  }
  return new Promise((resolve, reject) => canvas.toBlob(b => (b ? resolve(b) : reject(new Error('Could not make the image.'))), 'image/png'));
}

// Save a blob through a temporary <a download>.
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.hidden = true;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
