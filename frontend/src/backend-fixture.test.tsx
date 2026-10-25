import { readFileSync, writeFileSync } from "node:fs";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { EntryDetail, tokenLabel, type Entry } from "./EntryDetail";
import { safeURL } from "./ui";

afterEach(cleanup);
const fallback: Entry = {
  id: "sample",
  citation_key: "sample",
  entry_type: "article",
  status: "completed",
  fields: { title: "<img src=x onerror=alert(1)>", doi: "10.1000/example" },
  raw: "@article{sample}",
  result: {
    identity_state: "unique",
    field_state: "consistent",
    doi_state: "unverified",
    evidence: [
      {
        id: "e1",
        provider: "Fixture",
        method: "mock",
        fields: { title: "Evidence title" },
        url: "javascript:alert(1)",
      },
    ],
    assessments: [
      {
        field: "title",
        original: "Before",
        suggested: "After",
        judgment: "correction",
        evidence_ids: ["e1"],
      },
    ],
    calls: [
      {
        provider: "llm",
        status: "ok",
        tokens: { prompt_tokens: 10, completion_tokens: 5 },
      },
      { provider: "cnki", status: "no_results" },
      { provider: "cnki", operation: "fetch", status: "error" },
    ],
  },
};
const props = {
  mode: "mock" as const,
  onManual: vi.fn(),
  onRefresh: vi.fn(),
  notify: vi.fn(),
};

test("backend-generated mock entries render every detail tab without interpreting HTML", () => {
  const entries: Entry[] = process.env.CITE_AUDIT_FIXTURE
    ? JSON.parse(readFileSync(process.env.CITE_AUDIT_FIXTURE, "utf8")).entries
    : [fallback];
  if (process.env.CITE_AUDIT_FIXTURE) expect(entries).toHaveLength(8);
  for (const entry of entries) {
    for (const tab of [
      "comparison",
      "evidence",
      "original",
      "queries",
    ] as const) {
      const view = render(
        <EntryDetail {...props} entry={entry} initialTab={tab} />,
      );
      expect(screen.getByRole("tabpanel").textContent?.length).toBeGreaterThan(
        0,
      );
      expect(view.container.querySelector("img")).toBeNull();
      expect(view.container.querySelector("script")).toBeNull();
      for (const anchor of view.container.querySelectorAll("a")) {
        expect(safeURL(anchor.href)).not.toBeNull();
        expect(anchor.rel).toContain("noopener");
      }
      view.unmount();
    }
  }
  if (process.env.CITE_AUDIT_FIXTURE_REPORT) {
    writeFileSync(
      process.env.CITE_AUDIT_FIXTURE_REPORT,
      JSON.stringify({ entries: entries.length, tabs: entries.length * 4 }),
      "utf8",
    );
  }
});

test("unverified DOI remains visible, references select evidence, and keyboard tabs work", () => {
  render(<EntryDetail {...props} entry={fallback} />);
  expect(screen.getAllByText("未核验").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "[1]" }));
  expect(
    screen.getByRole("tab", { name: /证据/ }).getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.getByText("模拟证据")).toBeDefined();
  expect(screen.queryByRole("link", { name: /来源/ })).toBeNull();
  const evidence = screen.getByRole("tab", { name: /证据/ });
  fireEvent.keyDown(evidence, { key: "ArrowRight" });
  expect(
    screen.getByRole("tab", { name: "原始条目" }).getAttribute("aria-selected"),
  ).toBe("true");
  fireEvent.keyDown(screen.getByRole("tab", { name: "原始条目" }), {
    key: "End",
  });
  expect(
    screen.getByRole("tab", { name: "查询记录" }).getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.getByText("15 tokens")).toBeDefined();
  expect(screen.getByText("公开网页检索未命中")).toBeDefined();
  expect(screen.getByText("候选待核验")).toBeDefined();
});

test("safe links and usage formatting reject unsafe or unavailable values", () => {
  expect(safeURL("javascript:alert(1)")).toBeNull();
  expect(safeURL("https://user:secret@example.org")).toBeNull();
  expect(safeURL("https://example.org/paper")).toBe(
    "https://example.org/paper",
  );
  expect(tokenLabel({ input_tokens: 2, output_tokens: 3 })).toBe("5 tokens");
  expect(tokenLabel({ total_tokens: 0 })).toBe("0 tokens");
  expect(tokenLabel({})).toBe("");
  expect(tokenLabel(null)).toBe("");
});

test("formal publication and unverified model leads stay separate from current suggestions", () => {
  const entry: Entry = {
    ...fallback,
    result: {
      ...fallback.result,
      publication_state: "candidate",
      suggested_bibtex: "@misc{current}",
      publication_suggestion: {
        suggested_bibtex: "@article{published}",
        fields: { title: "Published edition" },
        evidence_ids: ["e1"],
        original_arxiv_id: "2401.12345",
      },
      model_suggestions: [
        {
          field: "year",
          value: 2025,
          reason: "Needs confirmation",
          evidence_ids: ["e1"],
        },
      ],
    },
  };
  render(<EntryDetail {...props} entry={entry} />);
  expect(screen.getByRole("heading", { name: "当前版本" })).toBeDefined();
  expect(screen.getByRole("heading", { name: "正式发表版本" })).toBeDefined();
  expect(screen.getByRole("button", { name: "复制建议 BibTeX" })).toBeDefined();
  expect(
    screen.getByRole("button", { name: "复制正式版 BibTeX" }),
  ).toBeDefined();
  expect(screen.getByText("模型证据线索 · 待人工核对")).toBeDefined();
  expect(screen.getByText(/未作为已核验修正/)).toBeDefined();
});

test("retained stale evidence is never presented as newly online verified", () => {
  const entry: Entry = {
    ...fallback,
    result: {
      ...fallback.result,
      evidence: [
        {
          id: "stale",
          provider: "ACL",
          method: "api",
          cache_state: "stale",
          revalidation_error: "timeout",
          last_checked_at: "2026-01-01T00:00:00Z",
          retrieved_at: "2025-01-01T00:00:00Z",
        },
      ],
    },
  };
  render(
    <EntryDetail {...props} mode="live" entry={entry} initialTab="evidence" />,
  );
  expect(screen.getByText("历史证据 · 复核失败")).toBeDefined();
  expect(screen.queryByText("在线获取")).toBeNull();
  expect(screen.getByText(/最近复核/)).toBeDefined();
});
