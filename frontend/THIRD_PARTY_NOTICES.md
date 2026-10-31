# UI references and acknowledgements

Cite Audit uses its own application components and styles, with accessible primitives from Radix UI. The interface structure follows patterns from [shadcn-admin](https://github.com/satnaing/shadcn-admin): Apps cards, Tasks tables and row actions, Settings forms, sidebar navigation, and shared dialog/select composition. No example users, integrations, or demo actions are included.

Referenced modules: `src/features/apps`, `src/features/tasks`, `src/features/settings`, `src/components/layout/nav-group.tsx`, and `src/components/ui`.

## shadcn-admin — MIT License

Copyright (c) 2024 Sat Naing

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Web fonts — SIL Open Font License 1.1

[Inter](https://github.com/google/fonts/tree/main/ofl/inter) and
[Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc) are
loaded from the Google Fonts CSS2 CDN. Both are licensed under the SIL Open
Font License 1.1; see their upstream [Inter license](https://github.com/google/fonts/blob/main/ofl/inter/OFL.txt)
and [Noto Sans SC license](https://github.com/google/fonts/blob/main/ofl/notosanssc/OFL.txt).
No font binaries are bundled or modified.

The request uses variable weights 400–700 (including the UI's 550/650 weights),
`display=swap`, and the CDN's Unicode-range subsets rather than a full Chinese
font download. User text is never sent as a font-subsetting parameter. System
sans-serif fallbacks remain available when the CDN cannot be reached; code
and BibTeX retain their separate monospace stack.
