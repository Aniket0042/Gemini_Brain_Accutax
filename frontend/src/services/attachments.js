/**
 * attachments.js — pure helpers for files attached in chat (no React, no network), so they can be tested
 * with `node --test`. The server decides what is really accepted; these checks only give instant errors.
 *
 * Plan: docs/DOCUMENT_UPLOAD_PLAN.md, phase 4.
 */

// What the picker offers when the server's config has not arrived (it normally lists the extensions).
export const DEFAULT_ACCEPT = ['.pdf', '.png', '.jpg', '.jpeg', '.webp', '.docx', '.xlsx', '.csv'];

const IMAGE_EXT = ['.png', '.jpg', '.jpeg', '.webp'];

export function extensionOf(name) {
  const m = /\.[^./\\]+$/.exec(String(name || '').toLowerCase());
  return m ? m[0] : '';
}

/** pdf, image, docx, xlsx, csv, or 'file' for anything else. By name: the server checks the bytes. */
export function fileKind(name) {
  const ext = extensionOf(name);
  if (ext === '.pdf') return 'pdf';
  if (IMAGE_EXT.includes(ext)) return 'image';
  if (ext === '.docx') return 'docx';
  if (ext === '.xlsx') return 'xlsx';
  if (ext === '.csv') return 'csv';
  return 'file';
}

/** The server's kind (pdf, png, jpeg, webp, docx, xlsx, csv) as the icon group: image counts as one. */
export function kindGroup(kind) {
  return ['png', 'jpeg', 'webp', 'image'].includes(kind) ? 'image' : kind || 'file';
}

export function humanSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/**
 * Instant checks before a file is sent. Returns { ok: true } or { ok: false, message }.
 * `config`: GET /uploads/config; `taken`: files already attached to this question.
 */
export function checkFile(file, config, taken = 0) {
  const accept = (config && config.accept && config.accept.length ? config.accept : DEFAULT_ACCEPT).map((e) => e.toLowerCase());
  const name = file && file.name ? file.name : 'This file';
  const maxFiles = (config && config.max_files) || 3;
  const maxBytes = (config && config.max_bytes) || 10 * 1024 * 1024;
  if (taken >= maxFiles) {
    return { ok: false, message: `You can attach up to ${maxFiles} files to one question.` };
  }
  if (!accept.includes(extensionOf(name))) {
    return { ok: false, message: `${name}: this type is not supported. Attach a PDF, a photo, a Word, Excel or CSV file.` };
  }
  if (!file.size) return { ok: false, message: `${name} is empty.` };
  if (file.size > maxBytes) {
    return { ok: false, message: `${name} is ${humanSize(file.size)}. The limit is ${humanSize(maxBytes)}.` };
  }
  return { ok: true };
}

/** A pasted screenshot has no name; give it one the server and the chip can show. */
export function nameForPasted(file, now = new Date()) {
  if (file && file.name && file.name !== 'image.png') return file.name;
  const ext = ({ 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp' })[file && file.type] || '.png';
  const pad = (n) => String(n).padStart(2, '0');
  return `pasted-image-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}${ext}`;
}

/** What the composer may do with its files right now. `items`: the chips (status uploading|reading|ready|failed). */
export function composerState(items) {
  const list = items || [];
  const uploading = list.filter((i) => i.status === 'uploading').length;
  const reading = list.filter((i) => i.status === 'reading').length;
  const failed = list.filter((i) => i.status === 'failed').length;
  const ready = list.filter((i) => i.status === 'ready');
  let blockedReason = '';
  if (uploading) blockedReason = 'Wait for the file to finish uploading.';
  else if (reading) blockedReason = 'Your file is still being read.';
  else if (failed) blockedReason = 'Remove the file that could not be read.';
  return { uploading, reading, failed, ready, busy: uploading + reading > 0, blocked: Boolean(blockedReason), blockedReason };
}

/** Starter questions for the files attached, by what the reader found. Fill the box; never send by themselves. */
export function starterPrompts(items) {
  const types = new Set((items || []).filter((i) => i.status === 'ready').map((i) => i.documentType || ''));
  if (!types.size) return [];
  const out = [];
  const bill = ['tax_invoice', 'simplified_tax_invoice', 'receipt'];
  if ([...types].some((t) => bill.includes(t))) {
    out.push('Read this bill', 'Is this a valid tax invoice? Can I claim the VAT?', 'Is this bill already in our books?');
  }
  if (types.has('statement')) out.push('Does this statement match our books?');
  if (types.has('contract')) out.push('What are the payment terms?', 'What are the key dates and obligations?');
  if (types.has('budget')) out.push('Compare this budget with our actuals', 'Where are we over or under budget?');
  if (!out.length) out.push('Summarize this file');
  return out;
}

/** The ids and chips a question carries: only files that are ready. */
export function attachmentsToSend(items) {
  const ready = (items || []).filter((i) => i.status === 'ready' && i.id);
  return {
    ids: ready.map((i) => i.id),
    chips: ready.map((i) => ({ id: i.id, name: i.name, size: i.size, kind: i.kind, pages: i.pages })),
  };
}

/** The attachments block of a saved message, as chips; [] when it has none. */
export function chipsFromBlocks(blocks) {
  const block = Array.isArray(blocks) ? blocks.find((b) => b && b.type === 'attachments') : null;
  return block && Array.isArray(block.items) ? block.items : [];
}

/** The message for a failed upload or request: the server's plain message, else a safe one. */
export function uploadErrorMessage(status, body) {
  const detail = body && body.detail;
  if (detail && typeof detail === 'object' && detail.message) return String(detail.message);
  if (typeof detail === 'string' && detail) return detail;
  if (status === 401) return 'Your session has expired. Please sign in again.';
  if (status === 413) return 'This file is too large.';
  if (status === 0) return 'Could not reach the server. Check your connection and try again.';
  return 'The file could not be uploaded. Try again.';
}
