// The record state bar (.stepper) and the time picker spinner (.time-stepper) each have their own
// class, so the picker's column layout cannot stack the state bar.
import { afterEach, describe, expect, it } from 'vitest';
import { localField, BaseField } from '../ui/fields';
import { cleanup, key, render } from './ui-helpers';

afterEach(cleanup);

const node = (globalThis as any).process;
const fs = node.getBuiltinModule('node:fs');
const STYLES: string = fs.readFileSync(`${node.cwd()}/src/evlet/ui/styles.css`, 'utf8');
// Each rule as [selector, body].
const rules = [...STYLES.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(match => [match[1].trim(), match[2]] as const);

describe('state bar and time picker classes', () => {
  it('never styles the state bar as a column', () => {
    const bar = rules.filter(([selector]) => selector.split(',').some(part => /(^|\s)\.stepper(\s|$|[.:\[])/.test(part.trim())));
    expect(bar.length).toBeGreaterThan(0);
    for (const [selector, body] of bar) {
      expect(body, selector).not.toMatch(/flex-direction:\s*column/);
      expect(selector, selector).not.toMatch(/\.stepper(-v|-l)?\s+\.(icon-btn|flip)/);
    }
    expect(bar.some(([selector, body]) => selector === '.stepper' && /display:flex/.test(body))).toBe(true);
  });

  it('keeps the time picker on its own classes and its spinbutton working', () => {
    const field = localField({ name: 'meeting', type: 'time', label: 'Meeting' }, '14:00:00');
    const host = render(() => BaseField({ field }));
    host.querySelector<HTMLInputElement>('input')!.click();
    const pop = document.querySelector<HTMLElement>('.pop')!;
    expect(pop.querySelector('.stepper')).toBeNull();
    expect(pop.querySelectorAll('.time-stepper').length).toBe(2);
    const hours = pop.querySelector<HTMLElement>('.time-stepper-v[role="spinbutton"]')!;
    key(hours, 'ArrowUp');
    expect(field.value.peek()).toBe('15:00:00');
    expect(rules.some(([selector, body]) => selector === '.time-stepper' && /flex-direction:\s*column/.test(body))).toBe(true);
  });
});
