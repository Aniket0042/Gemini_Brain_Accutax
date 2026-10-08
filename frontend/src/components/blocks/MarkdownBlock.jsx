import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { linkComponents } from '../markdownLinks';

export function MarkdownBlock({ block }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={linkComponents}>{block.text || ''}</ReactMarkdown>
    </div>
  );
}
