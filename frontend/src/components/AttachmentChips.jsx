import React from 'react';
import { AlertTriangle, File, FileImage, FileSpreadsheet, FileText, Loader2, X } from 'lucide-react';
import { humanSize, kindGroup } from '../services/attachments';
import { useThumbnail } from '../hooks/useFileBlob';

const ICONS = { pdf: FileText, docx: FileText, image: FileImage, xlsx: FileSpreadsheet, csv: FileSpreadsheet };
const LABELS = { pdf: 'PDF', docx: 'Word', xlsx: 'Excel', csv: 'CSV', image: 'Image' };

function KindIcon({ kind, size = 16 }) {
  const Icon = ICONS[kindGroup(kind)] || File;
  return <Icon size={size} aria-hidden="true" />;
}

function statusLine(item) {
  if (item.status === 'uploading') return `Uploading ${Math.round((item.progress || 0) * 100)}%`;
  if (item.status === 'reading') return 'Reading…';
  if (item.status === 'failed') return item.error || 'Could not be read';
  return item.summary && item.summary !== 'Document' ? item.summary : 'Ready';
}

/** What is wrong with a file that was read, each cause as a plain sentence: the checks that failed, then any note. */
function causes(item) {
  const list = Array.isArray(item.problems) && item.problems.length
    ? [...item.problems]
    : (item.checksFailed ? [`${item.checksFailed} ${item.checksFailed === 1 ? 'problem was' : 'problems were'} found.`] : []);
  if (item.note) list.push(item.note);
  return list;
}

/**
 * The card that opens over a tile with a problem, on hover or keyboard focus: a rounded pill with the count, then
 * each cause in words. It replaces the browser's plain tooltip, which could only say "1 check failed".
 */
function Problems({ id, list, count }) {
  const n = count || list.length;
  return (
    <span id={id} role="tooltip" className="attach-pop">
      <span className="attach-pop-pill"><AlertTriangle size={12} aria-hidden="true" />{n} {n === 1 ? 'problem' : 'problems'} to look at</span>
      <ul>
        {list.map((line) => <li key={line}>{line}</li>)}
      </ul>
    </span>
  );
}

/**
 * One file as a tile, like a chat app's: a picture of the file (the image itself, a PDF's first page, or an
 * icon for Word, Excel and CSV) with its name under it. Clicking it opens the preview. While it uploads or is
 * read, the picture shows the progress; a file that could not be read says why on its face.
 */
function Tile({ item, token, onOpen, onRemove, sent }) {
  const group = kindGroup(item.kind);
  const thumb = useThumbnail(item, token);
  const state = sent ? 'ready' : item.status;
  const list = !sent && state === 'ready' ? causes(item) : [];
  const warn = list.length > 0;
  const popId = `attach-pop-${item.key || item.id}`;
  const isImage = group === 'image';
  const name = item.name;
  // a tile with a problem has the card below instead of the browser's tooltip
  const tooltip = warn ? undefined : [name, sent ? humanSize(item.size) : statusLine(item)].filter(Boolean).join('\n');

  return (
    <div className={`attach-tile is-${state}${isImage ? ' is-image' : ''}${warn ? ' has-warning' : ''}${sent ? ' is-sent' : ''}`}
         role="listitem">
      <button type="button" className="attach-tile-open" onClick={() => onOpen && onOpen(item)} title={tooltip}
              aria-label={`Preview ${name}`} aria-describedby={warn ? popId : undefined}>
        <span className="attach-tile-face">
          {thumb
            ? <img src={thumb} alt="" className="attach-tile-img" draggable="false" />
            : <span className="attach-tile-icon"><KindIcon kind={item.kind} size={28} /></span>}
          {!sent && (state === 'uploading' || state === 'reading') && (
            <span className="attach-tile-overlay" aria-hidden="true">
              <Loader2 size={18} className="attach-spin" />
              <span>{state === 'uploading' ? `${Math.round((item.progress || 0) * 100)}%` : 'Reading'}</span>
            </span>
          )}
          {!sent && state === 'failed' && (
            <span className="attach-tile-overlay is-error">
              <AlertTriangle size={18} aria-hidden="true" />
              <span>{item.error || 'Could not be read'}</span>
            </span>
          )}
          {warn && <span className="attach-tile-badge" aria-hidden="true"><AlertTriangle size={12} /></span>}
        </span>
        {!isImage && (
          <span className="attach-tile-bar">
            <span className={`attach-tile-kind is-${group}`}><KindIcon kind={item.kind} size={13} /></span>
            <span className="attach-tile-name">{name}</span>
          </span>
        )}
      </button>
      {onRemove && (
        <button type="button" className="attach-tile-remove" onClick={() => onRemove(item.key)}
                aria-label={`Remove ${name}`} title="Remove">
          <X size={12} />
        </button>
      )}
      {warn && <Problems id={popId} list={list} count={item.checksFailed} />}
      {!sent && (
        <span className="attach-sr" role="status">
          {name}: {statusLine(item)}{warn ? `. ${list.join(' ')}` : ''}
        </span>
      )}
    </div>
  );
}

/** The files attached to the question being written, as tiles above the box. */
export function AttachmentChips({ items, token, onRemove, onOpen }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="attach-row" role="list" aria-label="Attached files">
      {items.map((item) => (
        <Tile key={item.key} item={item} token={token} onOpen={onOpen} onRemove={onRemove} />
      ))}
    </div>
  );
}

/** The files a sent question carried, as tiles above its message. Clicking one opens the preview. */
export function MessageAttachments({ items, token, onOpen }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="attach-sent" role="list" aria-label="Attached files">
      {items.map((item) => (
        <Tile key={item.id} item={item} token={token} onOpen={onOpen} sent />
      ))}
    </div>
  );
}

export { LABELS as KIND_LABELS };

/** Starter questions: they fill the box, the user sends. */
export function StarterPrompts({ prompts, onPick }) {
  if (!prompts || prompts.length === 0) return null;
  return (
    <div className="attach-starters" role="list" aria-label="Suggested questions">
      {prompts.map((text) => (
        <span key={text} role="listitem">
          <button type="button" className="attach-starter" onClick={() => onPick(text)}>{text}</button>
        </span>
      ))}
    </div>
  );
}
