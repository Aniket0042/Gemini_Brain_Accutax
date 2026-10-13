/**
 * pdfRender.js — PDF.js, loaded only when a PDF is first drawn (the library and its worker are large).
 * Used for the thumbnail of a PDF tile and for the page-by-page preview in the side panel.
 */
let pdfjsPromise = null;

async function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = (async () => {
      const pdfjs = await import('pdfjs-dist');
      const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url');
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      return pdfjs;
    })();
    pdfjsPromise.catch(() => { pdfjsPromise = null; });
  }
  return pdfjsPromise;
}

export async function openPdf(blob) {
  const pdfjs = await loadPdfjs();
  return pdfjs.getDocument({ data: await blob.arrayBuffer() }).promise;
}

/** Draw one page (counted from 1) into a new canvas that is `width` CSS pixels wide. */
export async function renderPage(pdf, number, width) {
  const page = await pdf.getPage(number);
  const base = page.getViewport({ scale: 1 });
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const viewport = page.getViewport({ scale: (width / base.width) * ratio });
  const canvas = document.createElement('canvas');
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${Math.round((canvas.height / canvas.width) * width)}px`;
  await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
  return canvas;
}

/** Page one as a small JPEG data URL, for the tile. */
export async function pdfThumbnail(blob, width = 240) {
  const pdf = await openPdf(blob);
  try {
    const canvas = await renderPage(pdf, 1, width);
    return canvas.toDataURL('image/jpeg', 0.72);
  } finally {
    pdf.destroy?.();
  }
}
