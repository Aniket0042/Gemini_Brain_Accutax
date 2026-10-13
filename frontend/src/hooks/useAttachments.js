import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { uploadFile, fetchUpload, deleteUpload } from '../services/api';
import { checkFile, composerState, fileKind, nameForPasted } from '../services/attachments';

const POLL_MS = 1500;
const MESSAGE_MS = 8000;

/** A chip from the server's UploadInfo. "uploaded" is a file the reader has not picked up yet: still reading. */
function fromInfo(info) {
  const status = info.status === 'ready' || info.status === 'failed' ? info.status : 'reading';
  return {
    id: info.id,
    name: info.name,
    size: info.size,
    kind: info.kind,
    pages: info.pages,
    status,
    progress: 1,
    // For a failed file the reason is the message; for a ready one it is a note ("only the text is available").
    error: status === 'failed' ? info.failure_reason || 'This file could not be read.' : '',
    note: status === 'ready' ? info.failure_reason || '' : '',
    summary: info.summary || '',
    checksFailed: info.checks_failed || 0,
    problems: Array.isArray(info.problems) ? info.problems : [],
    documentType: info.document_type || '',
  };
}

/**
 * The composer's files: upload each one as soon as it is chosen, then follow it while the server reads it.
 *
 * A chip exists only for a file the server accepted. A file the server refuses (wrong type, too many pages,
 * password-protected ...) shows its reason in `message` and leaves no chip. A chip can still end up `failed`
 * when reading fails; it blocks sending until it is removed, so no one sends a question believing a file was read.
 */
export function useAttachments({ config, sessionId, orgIds, token }) {
  const [items, setItems] = useState([]);
  const [message, setMessage] = useState('');
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const aborts = useRef(new Map());
  const counter = useRef(0);
  const scope = `${sessionId || ''}|${(orgIds || []).join(',')}`;
  const scopeRef = useRef(scope);

  const patch = useCallback((key, changes) => {
    setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...changes } : i)));
  }, []);

  const enabled = Boolean(config && config.enabled && sessionId);

  const addFiles = useCallback((fileList) => {
    if (!enabled) return;
    const problems = [];
    let count = itemsRef.current.length;
    for (const raw of Array.from(fileList || [])) {
      const file = raw.name && raw.name !== 'image.png' ? raw : new File([raw], nameForPasted(raw), { type: raw.type });
      const check = checkFile(file, config, count);
      if (!check.ok) {
        problems.push(check.message);
        continue;
      }
      count += 1;
      counter.current += 1;
      const key = `file-${counter.current}`;
      // `file` stays with the chip so it can be previewed at once, before the server has answered
      setItems((prev) => [...prev, { key, file, name: file.name, size: file.size, kind: fileKind(file.name), status: 'uploading', progress: 0 }]);
      const { promise, abort } = uploadFile(file, {
        sessionId, organizationIds: orgIds, token, onProgress: (p) => patch(key, { progress: p }),
      });
      aborts.current.set(key, abort);
      promise
        .then((info) => {
          aborts.current.delete(key);
          patch(key, fromInfo(info));
        })
        .catch((err) => {
          aborts.current.delete(key);
          if (err.aborted) return;
          setItems((prev) => prev.filter((i) => i.key !== key));
          setMessage(`${file.name}: ${err.message}`);
        });
    }
    setMessage(problems.join(' '));
  }, [enabled, config, sessionId, orgIds, token, patch]);

  const remove = useCallback((key) => {
    const item = itemsRef.current.find((i) => i.key === key);
    if (aborts.current.has(key)) {
      aborts.current.get(key)();
      aborts.current.delete(key);
    }
    if (item && item.id) deleteUpload(item.id, token);
    setItems((prev) => prev.filter((i) => i.key !== key));
  }, [token]);

  // After a question is sent its files belong to the thread: forget the chips, keep the files.
  const clear = useCallback(() => {
    setItems([]);
    setMessage('');
  }, []);

  // Follow files the server is still reading.
  const reading = items.some((i) => i.status === 'reading' && i.id);
  useEffect(() => {
    if (!reading) return undefined;
    let stopped = false;
    const timer = setInterval(async () => {
      for (const item of itemsRef.current.filter((i) => i.status === 'reading' && i.id)) {
        try {
          const info = await fetchUpload(item.id, token);
          if (!stopped) patch(item.key, fromInfo(info));
        } catch (err) {
          if (!stopped && err.status === 404) patch(item.key, { status: 'failed', error: 'This file is no longer available. Attach it again.' });
          // any other error is a blip: ask again next tick
        }
      }
    }, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [reading, token, patch]);

  // A different chat or organization selection: the files were for the old one.
  useEffect(() => {
    if (scopeRef.current === scope) return;
    scopeRef.current = scope;
    aborts.current.forEach((abort) => abort());
    aborts.current.clear();
    itemsRef.current.forEach((i) => i.id && deleteUpload(i.id, token));
    setItems([]);
    setMessage('');
  }, [scope, token]);

  useEffect(() => {
    if (!message) return undefined;
    const timer = setTimeout(() => setMessage(''), MESSAGE_MS);
    return () => clearTimeout(timer);
  }, [message]);

  const state = useMemo(() => composerState(items), [items]);
  return { items, message, dismissMessage: () => setMessage(''), addFiles, remove, clear, enabled, ...state };
}
