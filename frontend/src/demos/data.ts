import type { IconName } from './icons';
export interface Task {
  id: string;
  title: string;
  done: boolean;
}
export interface Project {
  id: number;
  name: string;
  client: string;
  owner: keyof typeof people;
  status: keyof typeof statusLabels;
  priority: 'high' | 'normal';
  due: string;
  color: keyof typeof colors;
  icon: IconName;
  description: string;
  tasks: Task[];
  archived?: boolean;
  updated?: number;
}
export interface Activity {
  id: string;
  text: string;
  projectId: number;
  at: string;
  who: keyof typeof people;
}
export const people = {
  AK: { name: 'Aslam K.', role: 'Workspace admin', bg: '#efe4d7', fg: '#806340' },
  SK: { name: 'Sofia Khan', role: 'Design lead', bg: '#eadfff', fg: '#8c62b4' },
  AJ: { name: 'Alex James', role: 'Product designer', bg: '#d7ede9', fg: '#4c8d7d' },
  DN: { name: 'Dina Noor', role: 'Project lead', bg: '#f9dfdd', fg: '#b77b76' },
};
export const colors = {
  violet: { bg: '#eee9ff', fg: '#7b5edb', label: 'Violet' },
  blue: { bg: '#e6f0ff', fg: '#478be0', label: 'Blue' },
  pink: { bg: '#fce6ef', fg: '#d16c9d', label: 'Pink' },
  orange: { bg: '#fff0de', fg: '#d3983d', label: 'Orange' },
  teal: { bg: '#dcf3ef', fg: '#39a28e', label: 'Teal' },
};
export const statusLabels = { active: 'In progress', planned: 'Planned', complete: 'Completed' };
const makeTasks = (titles: string[], n: number): Task[] =>
  titles.map((title, i) => ({ id: 't' + (i + 1), title, done: i < n }));
export const initial: Project[] = [
  {
    id: 1,
    name: 'Orbit brand launch',
    client: 'Orbit Studio',
    owner: 'AK',
    status: 'active',
    priority: 'high',
    due: '2026-11-04',
    color: 'violet',
    icon: 'orbit',
    description:
      'A fresh identity for an ambitious next chapter. Bring the brand to life across every touchpoint.',
    tasks: makeTasks(
      [
        'Brand discovery & direction',
        'Identity exploration',
        'Logo system & guidelines',
        'Color & typography',
        'Social launch toolkit',
        'Website handoff',
        'Final stakeholder review',
        'Go live, make a little noise',
      ],
      5,
    ),
  },
  {
    id: 2,
    name: 'Website refresh',
    client: 'Luma Interiors',
    owner: 'SK',
    status: 'active',
    priority: 'normal',
    due: '2026-11-02',
    color: 'blue',
    icon: 'web',
    description:
      'A warmer, clearer digital home for Luma. Make the work the hero and the next step effortless.',
    tasks: makeTasks(
      [
        'Content audit',
        'Wireframes & page flow',
        'Visual direction',
        'Build key pages',
        'Responsive polish',
      ],
      3,
    ),
  },
  {
    id: 3,
    name: 'Mobile onboarding',
    client: 'Forma',
    owner: 'AJ',
    status: 'planned',
    priority: 'normal',
    due: '2026-11-12',
    color: 'pink',
    icon: 'phone',
    description:
      'Turn first-time visitors into confident users with a short, thoughtful onboarding experience.',
    tasks: makeTasks(
      [
        'Map the first-run journey',
        'Explore interaction patterns',
        'Build the prototype',
        'Test with five people',
        'Refine & hand off',
      ],
      1,
    ),
  },
  {
    id: 4,
    name: 'Q4 sales playbook',
    client: 'Internal · Growth',
    owner: 'AK',
    status: 'active',
    priority: 'high',
    due: '2026-10-30',
    color: 'orange',
    icon: 'book',
    description:
      'Give the team one practical place for the conversations, resources, and follow-ups that matter.',
    tasks: makeTasks(
      [
        'Define the sales stages',
        'Collect customer stories',
        'Write conversation guides',
        'Review with the team',
      ],
      3,
    ),
  },
  {
    id: 5,
    name: 'Customer portal',
    client: 'Northline',
    owner: 'DN',
    status: 'active',
    priority: 'normal',
    due: '2026-11-06',
    color: 'teal',
    icon: 'grid',
    description:
      'Help customers find answers, manage requests, and see what happens next without a phone call.',
    tasks: makeTasks(
      [
        'Customer interviews',
        'Information architecture',
        'Design core journeys',
        'Build & integrate',
        'Customer acceptance',
      ],
      2,
    ),
  },
  {
    id: 6,
    name: 'Design system',
    client: 'Internal · Product',
    owner: 'SK',
    status: 'active',
    priority: 'normal',
    due: '2026-11-09',
    color: 'violet',
    icon: 'layers',
    description:
      'A small, expressive set of foundations and components. Consistent where it helps. Flexible where it matters.',
    tasks: makeTasks(
      [
        'Design principles',
        'Color foundations',
        'Type & spacing',
        'Buttons & inputs',
        'Navigation patterns',
        'Table & detail patterns',
        'Accessibility review',
      ],
      5,
    ),
  },
  {
    id: 7,
    name: 'Partner programme',
    client: 'Altitude',
    owner: 'AJ',
    status: 'planned',
    priority: 'normal',
    due: '2026-11-16',
    color: 'orange',
    icon: 'people',
    description: 'Make it easy for the right partners to join, learn, and grow with Altitude.',
    tasks: makeTasks(
      ['Programme structure', 'Partner welcome kit', 'Registration experience', 'Launch checklist'],
      0,
    ),
  },
  {
    id: 8,
    name: 'Autumn campaign',
    client: 'Morrow',
    owner: 'DN',
    status: 'complete',
    priority: 'normal',
    due: '2026-10-27',
    color: 'pink',
    icon: 'star',
    description:
      'A bright seasonal campaign with a simple message and a little unexpected delight.',
    tasks: makeTasks(
      [
        'Campaign concept',
        'Copy & art direction',
        'Creative production',
        'Launch & monitor',
        'Results wrap-up',
      ],
      5,
    ),
  },
];
