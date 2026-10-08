import React from 'react';

/**
 * ReactMarkdown `a` renderer shared by every markdown view: a link to another page (an invoice,
 * bill or journal in Accutax, an FTA document) opens in a new tab, so the chat stays open.
 * In-page links (#anchors) keep their default behaviour.
 */
export function MarkdownLink({ node, href, children, ...props }) {
  if (/^https?:\/\//i.test(href || '')) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
        {children}
      </a>
    );
  }
  return <a href={href} {...props}>{children}</a>;
}

export const linkComponents = { a: MarkdownLink };
