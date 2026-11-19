/**
 * Copies text and says whether it worked.
 *
 * The async Clipboard API exists only in a secure context (HTTPS or localhost) and, inside an iframe, only with the
 * clipboard-write permission. The VM serves the app over plain HTTP, so there the API is missing and every button
 * that called it did nothing. The fallback copies through a hidden textarea and execCommand('copy'), which still
 * works on HTTP because it runs inside the click. Callers show "Copied" only when this returns true.
 */
export async function copyText(text) {
  const value = text == null ? '' : String(text);
  if (!value) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    /* refused (permissions, iframe): fall through to the textarea */
  }
  const active = document.activeElement;
  const area = document.createElement('textarea');
  try {
    area.value = value;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '0';
    area.style.left = '0';
    area.style.width = '1px';
    area.style.height = '1px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.focus();
    area.select();
    area.setSelectionRange(0, value.length); // iOS Safari selects nothing without this
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    area.remove();
    if (active && typeof active.focus === 'function') active.focus();
  }
}
