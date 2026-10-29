export const iconNames = [
  'grid',
  'check',
  'check-circle',
  'search',
  'plus',
  'chevron',
  'down',
  'activity',
  'tasks',
  'people',
  'calendar',
  'bolt',
  'list',
  'board',
  'filter',
  'sort',
  'download',
  'more',
  'close',
  'edit',
  'flag',
  'orbit',
  'web',
  'phone',
  'book',
  'layers',
  'star',
  'bell',
  'help',
  'arrow',
  'menu',
  'clock',
  'archive',
] as const;
export type IconName = (typeof iconNames)[number];
export function Icons() {
  return (
    <svg class="ev-defs" aria-hidden="true">
      <defs>
        <symbol id="ev-demo-i-grid" viewBox="0 0 24 24">
          <rect x="3" y="3" width="7" height="7" rx="2" />
          <rect x="14" y="3" width="7" height="7" rx="2" />
          <rect x="3" y="14" width="7" height="7" rx="2" />
          <rect x="14" y="14" width="7" height="7" rx="2" />
        </symbol>
        <symbol id="ev-demo-i-check" viewBox="0 0 24 24">
          <path d="m5 12 4 4L19 6" />
        </symbol>
        <symbol id="ev-demo-i-check-circle" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9" />
          <path d="m8 12 3 3 5-6" />
        </symbol>
        <symbol id="ev-demo-i-search" viewBox="0 0 24 24">
          <circle cx="10.8" cy="10.8" r="6.8" />
          <path d="m16 16 4.5 4.5" />
        </symbol>
        <symbol id="ev-demo-i-plus" viewBox="0 0 24 24">
          <path d="M12 5v14M5 12h14" />
        </symbol>
        <symbol id="ev-demo-i-chevron" viewBox="0 0 24 24">
          <path d="m9 5 7 7-7 7" />
        </symbol>
        <symbol id="ev-demo-i-down" viewBox="0 0 24 24">
          <path d="m7 10 5 5 5-5" />
        </symbol>
        <symbol id="ev-demo-i-activity" viewBox="0 0 24 24">
          <path d="M3 12h4l3-7 4 14 3-7h4" />
        </symbol>
        <symbol id="ev-demo-i-tasks" viewBox="0 0 24 24">
          <path d="m3 6 2 2 3-4M11 6h10M3 13h4M11 13h10M3 20h4M11 20h10" />
        </symbol>
        <symbol id="ev-demo-i-people" viewBox="0 0 24 24">
          <circle cx="9" cy="8" r="3" />
          <path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6M17 15a5 5 0 0 1 4 5" />
        </symbol>
        <symbol id="ev-demo-i-calendar" viewBox="0 0 24 24">
          <rect x="3" y="5" width="18" height="16" rx="3" />
          <path d="M7 3v4M17 3v4M3 10h18M8 14h2M14 14h2M8 18h2" />
        </symbol>
        <symbol id="ev-demo-i-bolt" viewBox="0 0 24 24">
          <path d="m13 2-9 12h7l-1 8 10-13h-7z" />
        </symbol>
        <symbol id="ev-demo-i-list" viewBox="0 0 24 24">
          <path d="M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1" />
        </symbol>
        <symbol id="ev-demo-i-board" viewBox="0 0 24 24">
          <rect x="3" y="4" width="5" height="16" rx="1.5" />
          <rect x="10" y="4" width="5" height="11" rx="1.5" />
          <rect x="17" y="4" width="4" height="14" rx="1.5" />
        </symbol>
        <symbol id="ev-demo-i-filter" viewBox="0 0 24 24">
          <path d="M4 7h16M7 12h10M10 17h4" />
          <circle cx="8" cy="7" r="2" fill="currentColor" stroke="none" />
          <circle cx="15" cy="12" r="2" fill="currentColor" stroke="none" />
        </symbol>
        <symbol id="ev-demo-i-sort" viewBox="0 0 24 24">
          <path d="M8 4v16m-4-4 4 4 4-4M16 20V4m-4 4 4-4 4 4" />
        </symbol>
        <symbol id="ev-demo-i-download" viewBox="0 0 24 24">
          <path d="M12 3v12m-4-4 4 4 4-4M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" />
        </symbol>
        <symbol id="ev-demo-i-more" viewBox="0 0 24 24">
          <circle cx="5" cy="12" r="1" />
          <circle cx="12" cy="12" r="1" />
          <circle cx="19" cy="12" r="1" />
        </symbol>
        <symbol id="ev-demo-i-close" viewBox="0 0 24 24">
          <path d="m6 6 12 12M6 18 18 6" />
        </symbol>
        <symbol id="ev-demo-i-edit" viewBox="0 0 24 24">
          <path d="m14 5 5 5M4 20l5-1L20 8a3.5 3.5 0 0 0-5-5L4 14z" />
        </symbol>
        <symbol id="ev-demo-i-flag" viewBox="0 0 24 24">
          <path d="M5 21V4c5-4 9 5 15 0v11c-6 5-10-4-15 0" />
        </symbol>
        <symbol id="ev-demo-i-orbit" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="3" />
          <ellipse cx="12" cy="12" rx="10" ry="5" transform="rotate(-40 12 12)" />
          <path d="M6 7a8 8 0 1 1 11 11" />
        </symbol>
        <symbol id="ev-demo-i-web" viewBox="0 0 24 24">
          <rect x="3" y="4" width="18" height="16" rx="3" />
          <path d="M3 9h18M7 6.5h.01M10 6.5h.01M7 13h4M7 16h7" />
        </symbol>
        <symbol id="ev-demo-i-phone" viewBox="0 0 24 24">
          <rect x="6" y="2" width="12" height="20" rx="3" />
          <path d="M10 5h4M11 18h2" />
        </symbol>
        <symbol id="ev-demo-i-book" viewBox="0 0 24 24">
          <path d="M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-3-1H4zM13 7a3 3 0 0 1 3-3h5v16h-5a4 4 0 0 0-3 1" />
        </symbol>
        <symbol id="ev-demo-i-layers" viewBox="0 0 24 24">
          <path d="m12 3 10 6-10 6L2 9zM3 14l9 5 9-5M3 18l9 5 9-5" />
        </symbol>
        <symbol id="ev-demo-i-star" viewBox="0 0 24 24">
          <path d="m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z" />
        </symbol>
        <symbol id="ev-demo-i-bell" viewBox="0 0 24 24">
          <path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 8h18c0-1-3-1-3-8M10 21h4" />
        </symbol>
        <symbol id="ev-demo-i-help" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9" />
          <path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 17h.01" />
        </symbol>
        <symbol id="ev-demo-i-arrow" viewBox="0 0 24 24">
          <path d="M5 12h14m-5-5 5 5-5 5" />
        </symbol>
        <symbol id="ev-demo-i-menu" viewBox="0 0 24 24">
          <path d="M4 6h16M4 12h16M4 18h16" />
        </symbol>
        <symbol id="ev-demo-i-clock" viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 6v6l4 2" />
        </symbol>
        <symbol id="ev-demo-i-archive" viewBox="0 0 24 24">
          <rect x="3" y="3" width="18" height="5" rx="1" />
          <path d="M5 8v13h14V8M10 12h4" />
        </symbol>
      </defs>
    </svg>
  );
}
export function Icon(props: { name: IconName; class?: string }) {
  return (
    <svg class={'ev-icon ' + (props.class ?? '')} aria-hidden="true">
      <use href={'#ev-demo-i-' + props.name} />
    </svg>
  );
}
