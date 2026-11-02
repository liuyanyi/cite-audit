import { expect, it } from 'vitest';
import { bibliographicText } from '../bibliographic-text';

it('unwraps only a balanced whole-title group for display', () => {
  expect(bibliographicText("{Let's Verify Step by Step}")).toBe("Let's Verify Step by Step");
  expect(bibliographicText('{Training {BERT} Models}')).toBe('Training {BERT} Models');
  expect(bibliographicText('{BERT} and {GPT}')).toBe('{BERT} and {GPT}');
  expect(bibliographicText('{Unbalanced {title}')).toBe('{Unbalanced {title}');
});

it.each(['&', '\\&', '\\&;', '&amp;', '&#38;', '&#038;', '&#x26;', '&amp;amp;', '\\&amp;'])(
  'displays %s as an ampersand in bibliographic text', encoded => {
    expect(bibliographicText(`Theory ${encoded} Applications`, 'journal')).toBe('Theory & Applications');
  },
);
it.each(['url', 'doi', 'eprint', 'citation_key', 'unknown_field'])('preserves %s exactly', field => {
  const raw = 'https://example.org/?x=1&amp;y=2\\&;';
  expect(bibliographicText(raw, field)).toBe(raw);
});
it('keeps markup, unrelated entities and TeX instructions literal', () => {
  const raw = '<img src=x onerror=alert(1)> &lt;script&gt; \\input{file}';
  expect(bibliographicText(raw)).toBe(raw);
});
it('preserves literal punctuation and handles missing values', () => {
  expect(bibliographicText('A &; B')).toBe('A &; B');
  expect(bibliographicText(null)).toBe('');
});
