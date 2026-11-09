// The Image field in forms: preview, Upload or Replace, Remove, the type check before upload, and its kind.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { conditionsFor } from '../app/filters';
import { FieldControl, ImageControl, localField } from '../ui/fields';
import { formatValue } from '../ui/kinds';
import type { ImageSource } from '../richtext/images';
import { cleanup, render } from './ui-helpers';

afterEach(cleanup);

const meta = { name: 'photo', type: 'image', label: 'Photo' };
const source = (): ImageSource & { upload: ReturnType<typeof vi.fn> } => ({
  url: id => `/api/item/01/attachments/${id}`,
  key: () => 'item/01/attachments',
  copy: async () => null,
  upload: vi.fn(async () => '42'),
});
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const pick = (control: HTMLElement, file: File) => {
  const input = control.querySelector<HTMLInputElement>('input[type=file]')!;
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  input.dispatchEvent(new Event('change'));
};

describe('Image field', () => {
  it('uploads a picked image and writes its id, then shows it with Replace and Remove', async () => {
    const field = localField(meta, null), images = source();
    const control = ImageControl(field, 'f1', {}, images);
    render(() => control.element);
    expect(control.element.querySelector('button')!.textContent).toBe('Upload');
    expect(control.element.querySelector<HTMLInputElement>('input[type=file]')!.accept).toBe('image/png,image/jpeg,image/gif,image/webp');
    pick(control.element, new File([new Uint8Array([137, 80])], 'shelf.png', { type: 'image/png' }));
    await settle(); await settle();
    expect(images.upload).toHaveBeenCalledOnce();
    expect(field.value.get()).toBe('42');
    expect(control.element.querySelector('img')!.getAttribute('src')).toBe('/api/item/01/attachments/42');
    const buttons = [...control.element.querySelectorAll('button')].map(button => button.textContent);
    expect(buttons).toEqual(['Replace', 'Remove']);
    control.element.querySelectorAll('button')[1].click();
    expect(field.value.get()).toBeNull();
  });
  it('refuses SVG before anything is uploaded', async () => {
    const field = localField(meta, null), images = source();
    const control = ImageControl(field, 'f2', {}, images);
    render(() => control.element);
    pick(control.element, new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' }));
    await settle();
    expect(images.upload).not.toHaveBeenCalled();
    expect(field.error.get()).toMatch(/SVG/);
  });
  it('cannot upload outside a record, and says when it is required', () => {
    const field = localField({ ...meta, required: true }, null);
    const control = FieldControl(field, {});
    render(() => control.element);
    expect(control.element.querySelector<HTMLButtonElement>('button')!.disabled).toBe(true);
    expect(control.commit()).toBe(false);
    expect(field.error.get()).toBe('Photo is required');
  });
  it('reads "Image" in lists and filters on is set', () => {
    expect(formatValue(meta as any, '7')).toBe('Image');
    expect(formatValue(meta as any, null)).toBe('');
    expect(conditionsFor({ ...meta, required: false, read_only: false, operators: ['isnull'] } as any).map(item => item.op)).toEqual(['set', 'notset']);
  });
});
