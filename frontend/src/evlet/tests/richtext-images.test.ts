// Images in the rich text editor: type checks (SVG refused), downscaling, upload placeholders with
// progress, the image tools, undo after an upload, and copy and paste between records (re-upload).
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cell } from '../reactive';
import { RichTextEditor, type RichTextEditor as Editor } from '../richtext/editor';
import { parseHTML } from '../richtext/html';
import { checkImage, fit, ImageError, prepareImage, type ImageSource } from '../richtext/images';
import { renderView } from '../richtext/render';
import { cleanup, render } from './ui-helpers';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const png = (name = 'shot.png') => new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], name, { type: 'image/png' });

function source(overrides: Partial<ImageSource> = {}) {
  let next = 40;
  const uploads: Array<{ name: string; progress: (fraction: number) => void; resolve: (id: string) => void; reject: (error: Error) => void }> = [];
  const images: ImageSource = {
    url: id => `/api/note/01abc/attachments/${id}`,
    key: () => 'note/01abc/attachments',
    copy: vi.fn(async () => new Blob([new Uint8Array([1, 2])], { type: 'image/png' })),
    upload: (file, name, progress) => new Promise((resolve, reject) => uploads.push({ name, progress, resolve, reject })),
    ...overrides,
  };
  return { images, uploads, id: () => String(next++) };
}

function setup(initial = '<p>Hi</p>', images?: ImageSource) {
  const value = cell<string | null>(initial);
  const changes: Array<string | null> = [];
  let editor!: Editor;
  render(() => {
    editor = RichTextEditor({ value: () => value.get(), onChange: next => { changes.push(next); value.set(next); }, label: 'Body', images });
    return editor.element;
  });
  editor.focus();
  return { editor, changes };
}

function paste(editor: Editor, data: { files?: File[]; html?: string; text?: string }) {
  const event = new Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'clipboardData', { value: {
    files: data.files ?? [], getData: (kind: string) => kind === 'text/html' ? data.html ?? '' : kind === 'text/plain' ? data.text ?? '' : '',
  } });
  editor.surface.dispatchEvent(event);
}

describe('checking and preparing images', () => {
  it('takes PNG, JPEG, GIF and WebP and refuses SVG', () => {
    expect(() => checkImage(png())).not.toThrow();
    expect(() => checkImage(new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' }))).toThrow(/SVG/);
    expect(() => checkImage(new File(['<svg/>'], 'logo.svg', { type: '' }))).toThrow(ImageError);
    expect(() => checkImage(new File(['x'], 'a.bmp', { type: 'image/bmp' }))).toThrow(/PNG, JPEG, GIF or WebP/);
  });
  it('fits the long side into 2000 px', () => {
    expect(fit(4000, 3000)).toEqual({ width: 2000, height: 1500, scaled: true });
    expect(fit(1200, 3600)).toEqual({ width: 667, height: 2000, scaled: true });
    expect(fit(800, 600)).toEqual({ width: 800, height: 600, scaled: false });
  });
  it('sends the file as it is when the browser cannot decode images', async () => {
    const file = png();
    expect(await prepareImage(file)).toEqual({ blob: file, name: 'shot.png' });
  });
  it('downscales and re-encodes as WebP, keeping transparency', async () => {
    const drawn: number[][] = [];
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 4000, height: 3000, close() {} })));
    vi.stubGlobal('OffscreenCanvas', class {
      constructor(public width: number, public height: number) {}
      getContext() { return { drawImage: (_: unknown, x: number, y: number, w: number, h: number) => drawn.push([w, h]), getImageData: () => ({ data: new Uint8ClampedArray([0, 0, 0, 0]) }) }; }
      convertToBlob(options: { type: string }) { return Promise.resolve(new Blob([new Uint8Array(10)], { type: options.type })); }
    });
    const prepared = await prepareImage(new File([new Uint8Array(5000)], 'photo.png', { type: 'image/png' }));
    expect(drawn).toEqual([[2000, 1500]]);
    expect(prepared.name).toBe('photo.webp');
    expect(prepared.blob.type).toBe('image/webp');
  });
  it('uses PNG for a transparent image when the browser cannot write WebP, JPEG otherwise', async () => {
    const encoded: string[] = [];
    const canvas = (alpha: number) => class {
      constructor(public width: number, public height: number) {}
      getContext() { return { drawImage() {}, getImageData: () => ({ data: new Uint8ClampedArray([0, 0, 0, alpha]) }) }; }
      convertToBlob(options: { type: string }) { encoded.push(options.type); return Promise.resolve(new Blob([new Uint8Array(10)], { type: options.type === 'image/webp' ? 'image/png' : options.type })); }
    };
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 3000, height: 100, close() {} })));
    vi.stubGlobal('OffscreenCanvas', canvas(0));
    expect((await prepareImage(new File([new Uint8Array(50)], 'a.png', { type: 'image/png' }))).name).toBe('a.png');
    vi.stubGlobal('OffscreenCanvas', canvas(255));
    expect((await prepareImage(new File([new Uint8Array(50)], 'b.jpg', { type: 'image/jpeg' }))).name).toBe('b.jpg');
    expect(encoded).toEqual(['image/webp', 'image/png', 'image/webp', 'image/jpeg']);
  });
});

describe('images in the editor', () => {
  it('has an Image button only where images can go', () => {
    expect(setup().editor.element.querySelector('[aria-label="Image"]')).toBeNull();
    cleanup();
    expect(setup('<p></p>', source().images).editor.element.querySelector('[aria-label="Image"]')).not.toBeNull();
  });
  it('pastes an image file in place with a progress placeholder, then stores only its attachment id', async () => {
    const { images, uploads } = source();
    const { editor, changes } = setup('<p>Hi</p>', images);
    paste(editor, { files: [png()] });
    expect(editor.surface.querySelector('.rt-pending progress')).not.toBeNull();
    // A placeholder is never stored.
    expect(changes.at(-1)).toBe('<p>Hi</p>');
    await flush();
    uploads[0].progress(0.5);
    expect(editor.surface.querySelector<HTMLProgressElement>('.rt-pending progress')!.value).toBe(0.5);
    uploads[0].resolve('41');
    await flush();
    expect(changes.at(-1)).toBe('<p>Hi</p><figure class="rt-md"><img data-attachment="41" alt=""></figure>');
    const img = editor.surface.querySelector('img')!;
    expect(img.getAttribute('src')).toBe('/api/note/01abc/attachments/41');
  });
  it('a failed upload removes its placeholder and says why', async () => {
    const { images, uploads } = source();
    const { editor, changes } = setup('<p>Hi</p>', images);
    paste(editor, { files: [png()] });
    await flush();
    uploads[0].reject(new ImageError('Use an image up to 2 MiB'));
    await flush();
    expect(editor.surface.querySelector('.rt-pending')).toBeNull();
    expect(editor.element.querySelector('.rt-err')!.textContent).toBe('Use an image up to 2 MiB');
    expect(changes.at(-1) ?? '<p>Hi</p>').toBe('<p>Hi</p>');
  });
  it('refuses an SVG file with a message', () => {
    const { editor } = setup('<p>Hi</p>', source().images);
    paste(editor, { files: [new File(['<svg onload="x()"/>'], 'logo.svg', { type: 'image/svg+xml' })] });
    expect(editor.surface.querySelector('figure')).toBeNull();
    expect(editor.element.querySelector('.rt-err')!.textContent).toMatch(/SVG images are not allowed/);
  });
  it('undo after an upload brings back the image, not a placeholder', async () => {
    const { images, uploads } = source();
    const { editor, changes } = setup('<p>Hi</p>', images);
    paste(editor, { files: [png()] });
    await flush();
    uploads[0].resolve('42');
    await flush();
    editor.surface.dispatchEvent(new InputEvent('beforeinput', { inputType: 'historyUndo', bubbles: true, cancelable: true }));
    expect(changes.at(-1)).toBe('<p>Hi</p>');
    editor.surface.dispatchEvent(new InputEvent('beforeinput', { inputType: 'historyRedo', bubbles: true, cancelable: true }));
    expect(changes.at(-1)).toBe('<p>Hi</p><figure class="rt-md"><img data-attachment="42" alt=""></figure>');
  });
  it('sizes, describes and removes an image with its tools', async () => {
    const { editor, changes } = setup('<figure class="rt-md"><img data-attachment="7" alt=""></figure><p>x</p>', source().images);
    const tool = (action: string) => editor.surface.querySelector(`[data-image-action="${action}"]`)!
      .dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    tool('rt-full');
    expect(changes.at(-1)).toBe('<figure class="rt-full"><img data-attachment="7" alt=""></figure><p>x</p>');
    tool('alt');
    const [alt, caption] = Array.from(document.querySelectorAll<HTMLInputElement>('.rt-link input'));
    alt.value = 'Site plan'; caption.value = '  Ground   floor ';
    document.querySelector('.rt-link')!.dispatchEvent(new Event('submit', { cancelable: true }));
    expect(changes.at(-1)).toBe('<figure class="rt-full"><img data-attachment="7" alt="Site plan"><figcaption>Ground floor</figcaption></figure><p>x</p>');
    tool('remove');
    expect(changes.at(-1)).toBe('<p>x</p>');
  });
  it('copy marks images with their record; a paste into the same record keeps the reference', () => {
    const { editor, changes } = setup('<p>a</p><figure class="rt-md"><img data-attachment="7" alt="x"></figure><p>b</p>', source().images);
    const blocks = Array.from(editor.surface.children);
    window.getSelection()!.setBaseAndExtent(blocks[0].firstChild!, 0, blocks[2].firstChild!, 1);
    document.dispatchEvent(new Event('selectionchange'));
    const data: Record<string, string> = {};
    const event = new Event('copy', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: { setData: (kind: string, value: string) => { data[kind] = value; } } });
    editor.surface.dispatchEvent(event);
    expect(data['text/html']).toContain('<figure data-record="note/01abc/attachments" class="rt-md"><img data-attachment="7" alt="x"></figure>');
    window.getSelection()!.collapse(editor.surface.children[2].firstChild!, 1);
    document.dispatchEvent(new Event('selectionchange'));
    paste(editor, { html: data['text/html'] });
    expect(changes.at(-1)!.match(/data-attachment="7"/g)).toHaveLength(2);
  });
  it('a paste from another record uploads the image again; one of unknown origin is left out', async () => {
    const { images, uploads } = source();
    const { editor, changes } = setup('<p>x</p>', images);
    paste(editor, { html: '<figure data-record="note/01zzz/attachments" class="rt-sm"><img data-attachment="9" alt="Plan"></figure><p>y</p><figure class="rt-md"><img data-attachment="10"></figure>' });
    expect(images.copy).toHaveBeenCalledWith('note/01zzz/attachments', '9');
    await flush();
    expect(uploads).toHaveLength(1);
    uploads[0].resolve('77');
    await flush();
    expect(changes.at(-1)).toBe('<p>x</p><figure class="rt-sm"><img data-attachment="77" alt="Plan"></figure><p>y</p>');
    expect(changes.at(-1)).not.toContain('data-attachment="9"');
    expect(changes.at(-1)).not.toContain('data-attachment="10"');
  });
});

describe('the read-only view', () => {
  it('loads images through the record route and marks a missing one', () => {
    const view = document.createElement('div');
    view.append(renderView(parseHTML('<figure class="rt-sm"><img data-attachment="5" alt="Plan"><figcaption>Floor</figcaption></figure>'), { imageUrl: id => `/api/note/1/attachments/${id}` }));
    document.body.append(view);
    const img = view.querySelector('img')!;
    expect(img.getAttribute('src')).toBe('/api/note/1/attachments/5');
    expect(view.querySelector('figure')!.className).toBe('rt-sm');
    img.dispatchEvent(new Event('error'));
    expect(view.querySelector('figure')!.classList.contains('rt-missing')).toBe(true);
    expect(img.alt).toBe('Plan (image no longer available)');
  });
  it('without an image source shows the alt text, never a URL', () => {
    const view = document.createElement('div');
    view.append(renderView(parseHTML('<figure class="rt-md"><img data-attachment="5" alt="Plan"></figure>')));
    expect(view.querySelector('img')).toBeNull();
    expect(view.textContent).toBe('Plan');
  });
});
