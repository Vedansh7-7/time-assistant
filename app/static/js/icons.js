// Tiny authored icon set: 20px viewBox, one 1.5px stroke, round caps and joins.
// Icons are decorative (aria-hidden) unless a label is given.
const NS = 'http://www.w3.org/2000/svg';

const P = {
  plus: ['M10 4v12', 'M4 10h12'],
  close: ['M5.5 5.5l9 9', 'M14.5 5.5l-9 9'],
  'chevron-left': ['M12 4.5 6.5 10l5.5 5.5'],
  'chevron-right': ['M8 4.5 13.5 10 8 15.5'],
  repeat: ['M4 9.5V8a3 3 0 0 1 3-3h8.5', 'M13 2.5 15.5 5 13 7.5', 'M16 10.5V12a3 3 0 0 1-3 3H4.5', 'M7 17.5 4.5 15 7 12.5'],
  check: ['M4.5 10.5 8 14l7.5-8'],
  clock: ['M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14z', 'M10 6.5V10l2.5 1.5'],
  alert: ['M10 3.5 2.75 16h14.5z', 'M10 8.5v3', 'M10 13.75v.01'],
  'arrow-up': ['M10 16V4', 'M5 9l5-5 5 5'],
  'arrow-down': ['M10 4v12', 'M5 11l5 5 5-5'],
  external: ['M11.5 3.5h5v5', 'M16.5 3.5 9.5 10.5', 'M14.5 11.5v4a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1h4'],
  more: ['M5 10h.01', 'M10 10h.01', 'M15 10h.01'],
  menu: ['M3.5 6h13', 'M3.5 10h13', 'M3.5 14h13'],
  calendar: ['M4.5 4.5h11a1.5 1.5 0 0 1 1.5 1.5v9.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 15.5V6a1.5 1.5 0 0 1 1.5-1.5z', 'M3 8.5h14', 'M7 2.75v3', 'M13 2.75v3'],
  inbox: ['M3 11 5 4.5h10l2 6.5v4.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z', 'M3 11h4l1 2h4l1-2h4'],
  list: ['M8 5.5h9', 'M8 10h9', 'M8 14.5h9', 'M3.25 5.5h1.5', 'M3.25 10h1.5', 'M3.25 14.5h1.5'],
  users: ['M7.5 4.25a2.75 2.75 0 1 0 0 5.5 2.75 2.75 0 0 0 0-5.5z', 'M2.5 16a5 5 0 0 1 10 0', 'M13 4.5a2.5 2.5 0 0 1 0 5', 'M14.75 12a4.5 4.5 0 0 1 2.75 4'],
  settings: ['M3 6h8', 'M15 6h2', 'M13 4v4', 'M3 10h2', 'M9 10h8', 'M7 8v4', 'M3 14h9', 'M16 14h1', 'M14 12v4'],
  send: ['M3.5 9.5 16.5 3.5 11 16.5 9 11z', 'M9 11l7.5-7.5'],
};

export function icon(name, { label = null, size = 20 } = {}) {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', name === 'more' ? '2.5' : '1.5');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('class', 'icon');
  if (label) { svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', label); }
  else { svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false'); }
  for (const d of P[name] || []) {
    const p = document.createElementNS(NS, 'path');
    p.setAttribute('d', d);
    svg.appendChild(p);
  }
  return svg;
}
