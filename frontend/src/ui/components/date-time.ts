export const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];
const pad = (value: number) => String(value).padStart(2, "0");
export function dateValue(
  year: number,
  month: number,
  day: number,
): string | null {
  if (year < 1000 || year > 9999 || month < 1 || month > 12 || day < 1)
    return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCMonth() === month - 1 && date.getUTCDate() === day
    ? `${year}-${pad(month)}-${pad(day)}`
    : null;
}
export function parseDate(input: string): string | null {
  const value = input.trim();
  if (!value) return "";
  let day: number, month: number, year: number;
  let match: RegExpMatchArray | null;
  if ((match = value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) {
    [, year, month, day] = match.map(Number);
  } else if ((match = value.match(/^(\d{2})(\d{2})(\d{2}|\d{4})$/))) {
    day = +match[1];
    month = +match[2];
    year = match[3].length === 2 ? 2000 + +match[3] : +match[3];
  } else if (
    (match = value.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})$/))
  ) {
    day = +match[1];
    month = +match[2];
    year = match[3].length === 2 ? 2000 + +match[3] : +match[3];
  } else if ((match = value.match(/^(\d{1,2})\s+([a-z]+)\s+(\d{2}|\d{4})$/i))) {
    day = +match[1];
    month =
      MONTHS.findIndex(
        (name) => name.toLowerCase() === match![2].slice(0, 3).toLowerCase(),
      ) + 1;
    year = match[3].length === 2 ? 2000 + +match[3] : +match[3];
    const fullNames = [
      "january",
      "february",
      "march",
      "april",
      "may",
      "june",
      "july",
      "august",
      "september",
      "october",
      "november",
      "december",
    ];
    if (
      match[2].length !== 3 &&
      fullNames[month - 1] !== match[2].toLowerCase()
    )
      return null;
  } else return null;
  return dateValue(year!, month!, day!);
}
export function formatDate(value: string): string {
  const parsed = parseDate(value);
  if (!parsed) return value;
  const [year, month, day] = parsed.split("-").map(Number);
  return `${pad(day)} ${MONTHS[month - 1]} ${year}`;
}
export function parseTime(input: string): string | null {
  const value = input.trim().toLowerCase().replace(/\s+/g, "");
  if (!value) return "";
  const match = value.match(
    /^(\d{1,2})(?:[:.](\d{2})(?::(\d{2})(\.\d{1,6})?)?)?(am|pm)?$/,
  );
  let hour: number,
    minute: number,
    seconds = "",
    suffix = "";
  if (match) {
    hour = +match[1];
    minute = +(match[2] || 0);
    suffix = match[5] || "";
    if (match[3]) {
      if (+match[3] > 59) return null;
      seconds = ":" + match[3] + (match[4] || "");
    }
  } else {
    const compact = value.match(/^(\d{1,2})(\d{2})(am|pm)?$/);
    if (!compact) return null;
    hour = +compact[1];
    minute = +compact[2];
    suffix = compact[3] || "";
  }
  if (minute > 59 || (suffix ? hour < 1 || hour > 12 : hour > 23)) return null;
  if (suffix) hour = (hour % 12) + (suffix === "pm" ? 12 : 0);
  return `${pad(hour)}:${pad(minute)}${seconds}`;
}
export function todayValue() {
  const now = new Date();
  return dateValue(now.getFullYear(), now.getMonth() + 1, now.getDate())!;
}
export function offsetFor(date: string, time: string) {
  const [y, m, d] = date.split("-").map(Number),
    [h, min] = time.split(":").map(Number);
  const offset = -new Date(y, m - 1, d, h, min).getTimezoneOffset();
  return `${offset < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
}
export function dateTimeParts(input: string) {
  const match = input
    .trim()
    .match(
      /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)(Z|[+-]\d{2}:\d{2})?$/i,
    );
  if (!match || !parseDate(match[1]) || !parseTime(match[2])) return null;
  const zone = match[3]?.toUpperCase();
  if (zone && zone !== "Z" && (+zone.slice(1, 3) > 23 || +zone.slice(4) > 59))
    return null;
  return {
    date: match[1],
    time: match[2],
    offset: zone || offsetFor(match[1], match[2]),
  };
}
export function parseDateTime(
  input: string,
  existingOffset?: string,
): string | null {
  if (!input.trim()) return "";
  const iso = dateTimeParts(input);
  if (iso) {
    const explicitOffset = /(?:Z|[+-]\d{2}:\d{2})$/i.test(input.trim());
    return `${iso.date}T${iso.time}${explicitOffset ? iso.offset : existingOffset || iso.offset}`;
  }
  const match = input
    .trim()
    .match(
      /^(.+?)[,\s]+(\d{1,4}(?:[:.]\d{2})?(?::\d{2}(?:\.\d{1,6})?)?\s*(?:am|pm)?)(?:\s*(Z|[+-]\d{2}:\d{2}))?$/i,
    );
  if (!match) return null;
  const date = parseDate(match[1]),
    time = parseTime(match[2]);
  if (!date || !time) return null;
  const result = `${date}T${time}${match[3]?.toUpperCase() || existingOffset || offsetFor(date, time)}`;
  return dateTimeParts(result) ? result : null;
}
export function formatDateTime(value: string): string {
  const parsed = dateTimeParts(value);
  return parsed
    ? `${formatDate(parsed.date)}, ${parsed.time} ${parsed.offset}`
    : value;
}
export function addDays(value: string, days: number) {
  const [y, m, d] = value.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return (
    dateValue(
      date.getUTCFullYear(),
      date.getUTCMonth() + 1,
      date.getUTCDate(),
    ) || value
  );
}
export function monthDays(value: string) {
  const [year, month] = value.split("-").map(Number);
  const first = `${year}-${pad(month)}-01`;
  const weekday = (new Date(first + "T12:00:00Z").getUTCDay() + 6) % 7;
  return Array.from({ length: 6 }, (_, week) =>
    Array.from({ length: 7 }, (_, day) =>
      addDays(first, week * 7 + day - weekday),
    ),
  );
}
export function popupPosition(
  anchor: { left: number; top: number; bottom: number; width: number },
  size: { width: number; height: number },
  viewport: { width: number; height: number; left?: number; top?: number },
) {
  const edge = 8,
    gap = 6,
    leftEdge = viewport.left || 0,
    topEdge = viewport.top || 0;
  const width = Math.min(size.width, viewport.width - edge * 2);
  const below = topEdge + viewport.height - anchor.bottom - edge - gap;
  const above = anchor.top - topEdge - edge - gap;
  const bottom = below >= size.height || below >= above;
  const maxHeight = Math.max(40, bottom ? below : above);
  const height = Math.min(size.height, maxHeight);
  return {
    left: Math.max(
      leftEdge + edge,
      Math.min(anchor.left, leftEdge + viewport.width - width - edge),
    ),
    top: Math.max(
      topEdge + edge,
      bottom ? anchor.bottom + gap : anchor.top - gap - height,
    ),
    width,
    maxHeight,
    placement: bottom ? "bottom" : "top",
  };
}
