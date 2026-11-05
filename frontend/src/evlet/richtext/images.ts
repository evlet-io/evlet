// Images for the rich text editor: what may be added, downscaling before upload, and the upload itself.
//
// An image is a private attachment of the record (evlet/images.py). The browser shrinks it to at most
// 2000 px on the long side and re-encodes it (WebP when the browser can, else JPEG, or PNG when it has
// transparency) before upload; the server checks the type and size again whatever arrives.

import type { ApiClient } from '../api';

export const MAX_SIDE = 2000;
export const ACCEPT = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

/** Where the editor's images live: the record's attachments, or the person's holding area before it is saved. */
export interface ImageSource {
  /** The image's address, from its attachment id. */
  url(attachment: string): string;
  /** Upload one prepared image; resolves with its attachment id. */
  upload(file: Blob, name: string, progress: (fraction: number) => void): Promise<string>;
  /** This record's key, written into copied HTML so a paste elsewhere knows to upload the image again. */
  key(): string;
  /** The bytes of an image copied from another record (its key and id), read with the person's own access. */
  copy(key: string, attachment: string): Promise<Blob | null>;
}

export class ImageError extends Error {}

/** Refuse what is not a PNG, JPEG, GIF or WebP (SVG above all) before anything is read. */
export function checkImage(file: Blob & { name?: string }): void {
  const name = (file.name ?? '').toLowerCase();
  if (file.type === 'image/svg+xml' || name.endsWith('.svg') || name.endsWith('.svgz')) throw new ImageError('SVG images are not allowed. Use PNG, JPEG, GIF or WebP.');
  if (!ACCEPT.includes(file.type)) throw new ImageError('Use a PNG, JPEG, GIF or WebP image.');
}

/** Sizes after fitting the long side into max. */
export function fit(width: number, height: number, max = MAX_SIDE): { width: number; height: number; scaled: boolean } {
  const long = Math.max(width, height);
  if (long <= max) return { width, height, scaled: false };
  const ratio = max / long;
  return { width: Math.max(1, Math.round(width * ratio)), height: Math.max(1, Math.round(height * ratio)), scaled: true };
}

const encode = (canvas: HTMLCanvasElement | OffscreenCanvas, type: string, quality?: number): Promise<Blob | null> =>
  'convertToBlob' in canvas ? (canvas as OffscreenCanvas).convertToBlob({ type, quality }).catch(() => null)
    : new Promise(resolve => (canvas as HTMLCanvasElement).toBlob(blob => resolve(blob), type, quality));

/** Whether an image drawn on a canvas has any transparent pixel (sampled, so a large image stays quick). */
function transparent(context: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D, width: number, height: number): boolean {
  const data = context.getImageData(0, 0, width, height).data;
  const step = Math.max(1, Math.floor(data.length / 4 / 250000)) * 4;
  for (let index = 3; index < data.length; index += step) if (data[index] < 255) return true;
  return false;
}

/**
 * Shrink and re-encode an image for upload: at most 2000 px on the long side, WebP when the browser
 * encodes it (keeping transparency), else JPEG, or PNG for a transparent image. A GIF that needs no
 * shrinking is kept as it is, so it stays animated. Without canvas support the file goes as it is.
 */
export async function prepareImage(file: Blob & { name?: string }): Promise<{ blob: Blob; name: string }> {
  checkImage(file);
  const stem = (file.name ?? 'image').replace(/\.[a-z0-9]{1,5}$/i, '') || 'image';
  if (typeof createImageBitmap !== 'function') return { blob: file, name: file.name ?? 'image' };
  let bitmap: ImageBitmap;
  try { bitmap = await createImageBitmap(file); } catch { throw new ImageError('This image could not be read. Try another file.'); }
  const size = fit(bitmap.width, bitmap.height);
  if (!size.scaled && file.type === 'image/gif') { bitmap.close?.(); return { blob: file, name: file.name ?? `${stem}.gif` }; }
  const canvas: HTMLCanvasElement | OffscreenCanvas = typeof OffscreenCanvas === 'function' ? new OffscreenCanvas(size.width, size.height)
    : Object.assign(document.createElement('canvas'), { width: size.width, height: size.height });
  const context = canvas.getContext('2d') as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
  if (!context) { bitmap.close?.(); return { blob: file, name: file.name ?? stem }; }
  context.drawImage(bitmap, 0, 0, size.width, size.height);
  bitmap.close?.();
  const clear = file.type !== 'image/jpeg' && transparent(context, size.width, size.height);
  let blob = await encode(canvas, 'image/webp', 0.85);
  if (!blob || blob.type !== 'image/webp') blob = await encode(canvas, clear ? 'image/png' : 'image/jpeg', 0.85);
  if (!blob) return { blob: file, name: file.name ?? stem };
  // Re-encoding a small image can make it bigger: then the original goes.
  if (!size.scaled && blob.size >= file.size) return { blob: file, name: file.name ?? stem };
  const extension = blob.type === 'image/webp' ? 'webp' : blob.type === 'image/png' ? 'png' : 'jpg';
  return { blob, name: `${stem}.${extension}` };
}

async function base64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let text = '';
  for (let index = 0; index < bytes.length; index += 0x8000) text += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return btoa(text);
}

/**
 * Images of a record: uploads to `/api/{model}/{id}/attachments` (purpose image) once the record has
 * an id, and to the person's holding area `/api/{model}/_images` before that. id() is read at each call.
 */
export function recordImages(api: ApiClient, model: string, id: () => string | null): ImageSource {
  const base = () => { const record = id(); return record ? `/api/${model}/${record}/attachments` : `/api/${model}/_images`; };
  return {
    url: attachment => api.url(`${base()}/${attachment}`),
    key: () => base().slice(5),
    copy: async (key, attachment) => {
      const url = copiedUrl(api, key, attachment);
      if (!url) return null;
      const response = await fetch(url, { credentials: 'same-origin' });
      const blob = response.ok ? await response.blob() : null;
      return blob && ACCEPT.includes(blob.type) ? blob : null;
    },
    upload: (file, name, progress) => base64(file).then(content => new Promise<string>((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('POST', api.url(base()));
      request.setRequestHeader('Content-Type', 'application/json');
      request.setRequestHeader('Accept', 'application/json');
      request.setRequestHeader('X-Evlet-Request', '1');
      if (api.csrf) request.setRequestHeader('X-CSRF-Token', api.csrf);
      request.withCredentials = true;
      request.upload.onprogress = event => { if (event.lengthComputable) progress(event.loaded / event.total); };
      request.onload = () => {
        let body: any = null;
        try { body = JSON.parse(request.responseText); } catch { body = null; }
        if (request.status === 201 && body && typeof body.id === 'string') { progress(1); resolve(body.id); return; }
        reject(new ImageError(body?.fields?.image ?? (request.status === 413 ? 'This image is too large.' : 'The image could not be uploaded. Try again.')));
      };
      request.onerror = () => reject(new ImageError('The image could not be uploaded. Check the connection and try again.'));
      request.send(JSON.stringify({ name, content, purpose: 'image' }));
    })),
  };
}

/** The address of an image copied from another record (a data-record key and an id), when it is one of ours. */
export function copiedUrl(api: ApiClient, key: string, attachment: string): string | null {
  if (!/^[a-z][a-z0-9_]{0,62}\/(?:[0-9a-z]{1,26}\/attachments|_images)$/.test(key) || !/^[1-9][0-9]{0,18}$/.test(attachment)) return null;
  return api.url(`/api/${key}/${attachment}`);
}
