// Card numbers never leave the browser: a question is cleaned before it is shown, saved or sent.
// The server drops them too (backend privacy/sensitive.py) and recognises this marker as a dropped card,
// so the answer opens with the warning line and carries the on-screen notice.

export const CARD_REMOVED = '[card number removed]';

// 13–19 digits, spaces or dashes between them, not part of a longer number or an amount.
const CARD_RX = /(?<![\w.,-])(?<!\d\s)(?:\d[ -]?){12,18}\d(?!\w|[.,]\d|[\s-]\d)/g;

const luhn = (digits) => {
  let total = 0;
  for (let i = 0; i < digits.length; i += 1) {
    let n = Number(digits[digits.length - 1 - i]);
    if (i % 2) n = n > 4 ? n * 2 - 9 : n * 2;
    total += n;
  }
  return total % 10 === 0;
};

const isCard = (value) => {
  const digits = value.replace(/\D/g, '');
  return digits.length >= 13 && digits.length <= 19 && '3456'.includes(digits[0])
    && new Set(digits).size > 1 && luhn(digits);
};

// { text, cards }: the text with each card number replaced by the marker, and how many were removed.
export const dropCards = (text) => {
  let cards = 0;
  const cleaned = String(text || '').replace(CARD_RX, (match) => {
    if (!isCard(match)) return match;
    cards += 1;
    return CARD_REMOVED;
  });
  return { text: cleaned, cards };
};
