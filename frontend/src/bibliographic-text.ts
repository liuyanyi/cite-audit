// Readable text only: never parse HTML/TeX or mutate evidence and export values.
const textFields = new Set([
  'title', 'subtitle', 'booktitle', 'booksubtitle', 'journal', 'journaltitle',
  'shortjournal', 'author', 'editor', 'publisher', 'institution', 'organization',
  'school', 'series', 'address', 'location', 'note', 'abstract', 'keywords',
]);

export function bibliographicText(value: string | null | undefined, field = 'title'): string {
  if (!value || !textFields.has(field.toLowerCase())) return value ?? '';
  // Decode only ampersand entities, including bounded double encoding. Leave
  // other entities and markup as literal React text, and keep identifiers intact.
  let text = value;
  if (field.toLowerCase() === 'title' && text.startsWith('{') && text.endsWith('}')) {
    let depth = 0;
    for (let i = 0; i < text.length; i++) {
      if (text[i] === '\\') { i++; continue; }
      if (text[i] === '{') depth++;
      if (text[i] === '}' && --depth === 0) {
        if (i === text.length - 1) text = text.slice(1, -1);
        break;
      }
    }
  }
  for (let i = 0; i < 3; i++) {
    const decoded = text.replace(/&(?:amp|#0*38|#x0*26);/gi, '&');
    if (decoded === text) break;
    text = decoded;
  }
  // Some exported Bib records contain a stray entity terminator after \&.
  return text.replace(/\\&;?/g, '&');
}
