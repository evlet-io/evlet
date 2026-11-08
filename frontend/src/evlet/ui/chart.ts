// Bars or a line over rows, drawn to one scale as SVG: the report page's chart and the record page's
// chart blocks. Text goes in as text nodes and attributes, so nothing is parsed from markup.

import { formatNumber } from '../model/format';

/** One series: its label and, per row, its number and its value as text (for the tooltip). */
export interface ChartSeries<R> { label: string; value(row: R): number; text(row: R): string }

export interface ChartSpec<R> {
  type: 'bar' | 'line';
  rows: R[];
  /** The label of a row on the x axis. */
  label(row: R): string;
  /** One to four series, drawn as s0 to s3. */
  series: ChartSeries<R>[];
  /** What the chart shows, for screen readers. */
  title: string;
}

/** At most this many rows are drawn. */
export const CHART_ROWS = 40;

/** The chart as an SVG element sized by its viewBox. */
export function plotChart<R>(spec: ChartSpec<R>): SVGElement {
  const W = 720, H = 220, L = 56, R = 12, T = 16, B = 30;
  const list = spec.rows.slice(0, CHART_ROWS);
  const values = list.flatMap(row => spec.series.map(series => series.value(row) || 0));
  const top = niceTop(Math.max(0, ...values)), bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const y = (value: number) => T + (top - value) / span * (H - T - B);
  const band = (W - L - R) / Math.max(1, list.length);
  const tip = (row: R, series: ChartSeries<R>) => svg('title', {}, [`${spec.label(row)}: ${series.label} ${series.text(row)}`]);
  // Axis values are fractions of the scale: formatNumber takes them as fixed decimal text.
  const shortNumber = (value: number) => Math.abs(value) >= 1e6 ? `${formatNumber((value / 1e6).toFixed(1), 1)}M` : Math.abs(value) >= 1e3 ? `${formatNumber((value / 1e3).toFixed(1), 1)}k` : formatNumber(value.toFixed(0), 0);
  const parts: SVGElement[] = [];
  // Small whole counts get whole ticks (0 to 5, or every 2 to 10); anything else, quarters of the scale.
  const whole = bottom === 0 && top <= 10 && values.every(Number.isInteger);
  const ticks = whole ? Array.from({ length: top / (top <= 5 ? 1 : 2) + 1 }, (_, index) => index * (top <= 5 ? 1 : 2)) : [0, 0.25, 0.5, 0.75, 1].map(step => bottom + span * step);
  for (const value of ticks) {
    parts.push(svg('line', { class: 'grid', x1: L, x2: W - R, y1: y(value), y2: y(value) }),
      svg('text', { class: 'tick', x: L - 8, y: y(value) + 4, 'text-anchor': 'end' }, [shortNumber(value)]));
  }
  spec.series.forEach((series, s) => {
    const at = (row: R) => y(series.value(row) || 0);
    if (spec.type === 'line') {
      parts.push(svg('polyline', { class: `s${s}`, fill: 'none', 'stroke-width': 2, points: list.map((row, i) => `${L + band * (i + 0.5)},${at(row)}`).join(' ') }));
      list.forEach((row, i) => parts.push(svg('circle', { class: `s${s}`, r: 3, cx: L + band * (i + 0.5), cy: at(row) }, [tip(row, series)])));
      return;
    }
    const width = band * 0.7 / spec.series.length;
    list.forEach((row, i) => parts.push(svg('rect', { class: `s${s}`, rx: 3, x: L + band * (i + 0.15) + width * s, width: Math.max(1, width - 2),
      y: Math.min(at(row), y(0)), height: Math.abs(at(row) - y(0)) }, [tip(row, series)])));
  });
  const every = Math.ceil(list.length / 12);
  list.forEach((row, i) => { if (!(i % every)) parts.push(svg('text', { class: 'tick', x: L + band * (i + 0.5), y: H - 10, 'text-anchor': 'middle' }, [clip(spec.label(row), 14)])); });
  return svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': spec.title }, parts);
}

/** An SVG element; text children become text nodes, so nothing is parsed from markup. */
function svg(tag: string, attributes: Record<string, string | number>, children: Array<SVGElement | string> = []): SVGElement {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  element.append(...children);
  return element;
}

function niceTop(value: number) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  return [1, 2, 2.5, 5, 10].map(step => step * power).find(step => step >= value)!;
}
const clip = (text: string, size: number) => text.length > size ? `${text.slice(0, size - 1)}…` : text;
