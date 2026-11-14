import { afterEach, expect, it, vi } from 'vitest';
import { copyText } from '../ui/layers';
import { lastToast, stopAll, until } from './screens-helpers';
const original = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
afterEach(() => {
  stopAll();
  if (original) Object.defineProperty(navigator, 'clipboard', original);
  else Reflect.deleteProperty(navigator, 'clipboard');
});
const clipboard = (value: unknown) => Object.defineProperty(navigator, 'clipboard', { value, configurable: true });
it.each([false, true])('offers the exact selected draft when automatic copying is unavailable (denied=%s)', async denied => {
  clipboard(denied ? { writeText: async () => { throw new Error('Denied'); } } : undefined);
  const draft = '{"notes":"Keep <all> my edits", "rows":[1,2]}';
  await copyText(draft, 'your changes');
  const field = document.querySelector<HTMLTextAreaElement>('[role="dialog"] textarea')!;
  await until(() => expect(document.activeElement).toBe(field));
  expect(field.value).toBe(draft); expect(field.readOnly).toBe(true);
  expect(field.selectionStart).toBe(0); expect(field.selectionEnd).toBe(draft.length);
  expect(lastToast()).not.toContain('Copied');
});
it('reports successful copying only after the browser accepts the text', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined); clipboard({ writeText });
  await copyText('Exact record ID', 'the record ID');
  expect(writeText).toHaveBeenCalledWith('Exact record ID');
  expect(lastToast()).toBe('Copied the record ID');
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});
