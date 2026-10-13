/**
 * uploadBlobs.js — the bytes of files attached in chat, for previews and thumbnails.
 *
 * A file is fetched from the server once per page load and kept in memory only (never in storage): the server keeps
 * it as long as the chat thread, and a thread that is deleted takes its files with it.
 */
import { uploadErrorMessage } from './attachments';

const cache = new Map();

/** The file as a Blob, from the server. Cached by id; a failed fetch is not cached. */
export function getUploadBlob(id, token = '') {
  if (!cache.has(id)) {
    const promise = fetch(`/api/v1/uploads/${encodeURIComponent(id)}/file`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).then(async (response) => {
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const message = response.status === 404
          ? 'This file is no longer available. Files are kept only as long as the chat they were attached to.'
          : uploadErrorMessage(response.status, body);
        throw new Error(message);
      }
      return response.blob();
    });
    cache.set(id, promise);
    promise.catch(() => cache.delete(id));
  }
  return cache.get(id);
}

export function clearUploadBlobs() {
  cache.clear();
}

/** Save a Blob under a file name (the browser's own download). */
export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name || 'file';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
