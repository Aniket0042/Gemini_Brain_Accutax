import { useEffect, useState } from 'react';
import { getUploadBlob } from '../services/uploadBlobs';
import { kindGroup } from '../services/attachments';
import { pdfThumbnail } from '../services/pdfRender';

/**
 * The bytes of an attached file: the local File while it is still in the composer, else the server's copy.
 * Returns { blob, error, loading }.
 */
export function useFileBlob(item, token) {
  const [state, setState] = useState({ blob: item?.file || null, error: '', loading: !item?.file });
  const key = item?.file ? `local:${item.file.name}:${item.file.size}` : `server:${item?.id}`;

  useEffect(() => {
    if (!item) return undefined;
    if (item.file) {
      setState({ blob: item.file, error: '', loading: false });
      return undefined;
    }
    let cancelled = false;
    setState({ blob: null, error: '', loading: true });
    getUploadBlob(item.id, token)
      .then((blob) => { if (!cancelled) setState({ blob, error: '', loading: false }); })
      .catch((err) => { if (!cancelled) setState({ blob: null, error: err.message || 'The file could not be loaded.', loading: false }); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, token]);

  return state;
}

const thumbs = new Map();

/**
 * A picture for a tile: an image file as itself, a PDF as its first page. Other files have none (null).
 * Cached by file, so a tile that is drawn again does not render again.
 */
export function useThumbnail(item, token) {
  const group = kindGroup(item?.kind);
  const wanted = group === 'image' || group === 'pdf';
  const { blob } = useFileBlob(wanted ? item : null, token);
  const cacheKey = item?.id || (item?.file ? `${item.file.name}:${item.file.size}:${item.file.lastModified}` : '');
  const [url, setUrl] = useState(thumbs.get(cacheKey) || null);

  useEffect(() => {
    if (!wanted || !blob) return undefined;
    if (thumbs.has(cacheKey)) {
      setUrl(thumbs.get(cacheKey));
      return undefined;
    }
    let cancelled = false;
    let objectUrl = null;
    (async () => {
      try {
        if (group === 'image') {
          objectUrl = URL.createObjectURL(blob);
          if (!cancelled) setUrl(objectUrl);
        } else {
          const dataUrl = await pdfThumbnail(blob);
          thumbs.set(cacheKey, dataUrl);
          if (!cancelled) setUrl(dataUrl);
        }
      } catch {
        if (!cancelled) setUrl(null); // the tile falls back to its icon
      }
    })();
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [blob, group, wanted, cacheKey]);

  return wanted ? url : null;
}
