# Design: Relocate sidebar navigation to the editor panel

**Date**: 2026-08-09
**Status**: Draft — awaiting implementation plan
**Scope**: Move the left sidebar's entity nav row (编排/运行/设置/日志/统计 icons) **and** the project dropdown menu 项目▾ (打开项目/新建/打开目录) into the top of the right editor panel, leaving the entire left sidebar for the video list. Frontend-only; no JS logic changes (all wiring is selector/class based and location-independent).
**Approach**: HTML/CSS only.
**Related**: `clio/ui/static/index.html`, `clio/ui/static/style.css`, `clio/ui/static/src/layout.js`, `clio/ui/static/src/utils.js`, `clio/ui/static/src/main.js`, `docs/superpowers/specs/2026-08-08-sidebar-optimization-design.md` (previous sidebar rework: sticky top + video filters + accurate stage counts — all preserved).

## 1. Goals and non-goals

### Goals

1. **左侧全部让给视频**: after the move, `#sidebar` contains only the video section — 「视频」heading (with count) + 添加视频/选择视频 buttons + offline summary + search + chips + `#video-list` + `#stage-count-bar`. No nav icons, no project dropdown, no project name.
2. **右侧顶部聚合导航**: the right `#editor` panel carries the project menu and the entity nav at its top, above the existing `.tabs` (分析/口播/转录).
3. **零 JS 改动**: entity click dispatch, keyboard 1–5, active highlighting, project menu wiring all keep working untouched (verified below).
4. **折叠语义不变**: both panels fold/unfold exactly as today (Ctrl+B / Ctrl+\ / resize-handle click).

### Non-goals

- No JS logic changes (except handling the now-dead `#proj-name-sidebar` guard).
- No new backend routes; no changes to video list / filter / count-bar behavior (2026-08-08 design).
- Not persisting nav position as a setting — this is a stable one-time relocation.
- No thumbnail/download/processing-related changes.

### Success criteria

- Left `#sidebar` top has only a slim header with the collapse button ‹ (no project name / no nav).
- Right `#editor` header row shows: entity nav icon row (5 items, shortcuts 1–5) + project drop-down 项目▾ (打开项目 / 新建项目 / 打开目录), placed between the editor `panel-header` and the entity `.tabs`.
- Entity click switching and keyboard 1–5 behave exactly as today.
- Project menu 打开项目 / 新建项目 / 打开目录 all wire correctly from the new location.
- Both panels fold/unfold as before (Ctrl+B / Ctrl+\ and click-on-handle); `npm test` stays green.

## 2. Current behavior (verified)

- Left `#sidebar` (`index.html:36-102`) has a `panel-header` (project icon + `proj-name-sidebar` + `btn-collapse-sidebar`/`sidebar-project` block with the ‹ collapse button and `btn-project-menu` 项目▾ dropdown) and a `.sidebar-scroll` containing:
  - `#project-list.project-list-icons` — 5 entity icons (`index.html:53-79`), sticky at top, `style.css:561-575`
  - `h3` 视频 + `.video-list-actions` (添加视频/选择视频) + `offline-summary` + `video-filter-bar` + `#video-list` + `#stage-count-bar` (`index.html:80-101`)
- Right `#editor` (`index.html:151-179`) has `panel-header` (编辑器 + `btn-collapse-editor`), `.tabs` (分析/口播/转录, `style.css:1102-1132`), then tab panes, then `.editor-actions`.
- Entity switching: `main.js:345-355` binds `.project-item` clicks; `main.js:473-478` maps keys 1–5; `utils.js:85-114` `updateEntityUI` sets `#editor.entity-*` class and toggles `.project-item.active`.
- `#editor.entity-*` CSS (`style.css:1134-1146`) shows/hides `.tabs` and one pane per entity; the nav icon row must stay visible in every entity mode.
- Left collapse: `layout.js:28-37` `btn-collapse-sidebar` toggles `body.sidebar-collapsed`; `onResizeClick` (`layout.js:92-97`) does the same via handle click.

## 3. Design

### 3.1 HTML structure (`index.html`)

**Left `#sidebar` — slimmed to video only:**

```html
<aside id="sidebar">
  <div class="panel-header">
    <span class="panel-header-icon"><svg>…（video icon）…</svg></span>
    <button id="btn-collapse-sidebar" class="sidebar-btn" type="button" title="折叠侧边栏 (Ctrl+B)">‹</button>
  </div>
  <div class="sidebar-scroll">
    <h3>视频 <span id="video-count" class="muted"></span></h3>
    <div class="video-list-actions">…（添加视频 / 选择视频，unchanged）…</div>
    <div id="offline-summary" …></div>
    <div id="video-filter-bar" …></div>
    <ul id="video-list"></ul>
    <div id="stage-count-bar" …></div>
  </div>
</aside>
```

- Remove from the sidebar: `proj-name-sidebar`, the `sidebar-project` block (including `btn-project-menu` and `#project-menu`), and `#project-list`.
- Keep: `btn-collapse-sidebar` ‹ so the sidebar fold stays discoverable (symmetric with the editor's » collapse button). `layout.js` binding unchanged.

**Right `#editor` — nav + project menu at the top:**

```html
<aside id="editor">
  <div class="panel-header">
    <div class="panel-header-left">…（编辑器 icon + label，unchanged）…</div>
    <div class="panel-header-actions"><button id="btn-collapse-editor">»</button></div>
  </div>

  <!-- NEW: editor header row: entity nav + project menu -->
  <div class="editor-header-row">
    <ul id="project-list" class="project-list-icons">…（原 5 个 data-entity li，原样搬来）…</ul>
    <div class="sidebar-project">
      <button id="btn-project-menu" class="sidebar-btn" type="button" title="项目操作（打开/新建/打开目录）">项目 ▾</button>
      <div id="project-menu" class="project-menu" hidden>
        <button id="btn-open-project">打开项目</button>
        <button id="btn-new-project">新建项目</button>
        <button id="btn-reveal-project-sidebar">打开目录</button>
      </div>
    </div>
  </div>

  <div class="tabs">…（分析/口播/转录，unchanged）…</div>
  <div id="tab-texts" …></div>
  <div id="tab-voiceover" …></div>
  <div id="tab-transcript" …></div>
  <div id="tab-plan" …></div>
  <div id="tab-run" …></div>
  <div id="tab-config" …></div>
  <div id="tab-logs" …></div>
  <div id="tab-tokens" …></div>

  <div class="editor-actions">…（保存按钮，unchanged）…</div>
</aside>
```

- `#project-list` keeps `id` + `class="project-list-icons"` and the `data-entity` attributes, so `main.js`/`utils.js` selectors keep matching.
- `#btn-open-project/#btn-new-project/#btn-reveal-project-sidebar` keep their IDs; the existing onclick wiring in `main.js` (e.g. `btn-reveal-project-sidebar` at `main.js:421`) works unchanged.
- Project-menu close-on-outside-click and modal handling in `main.js` are selector based and unaffected.

### 3.2 CSS (`style.css`)

- New `.editor-header-row`:
  - `display:flex; align-items:center; gap:4px; padding:2px 6px; flex-shrink:0; border-bottom:1px solid var(--border);` — placed between `#editor .panel-header` and `.tabs`.
- For the moved `#project-list.project-list-icons`:
  - remove `position:sticky; top:0;` (no longer a scroll ancestor — the nav lives in a fixed header row, not the sidebar scroll container).
  - keep the flex-wrap single icon row layout (`flex:1; min-width:34px` compact icons). In a 400px editor the 5 items fit comfortably.
- `.sidebar-project` still right-aligns in its flex row (`margin-left:auto`), so 项目▾ sits at the right edge of the header row; the `.project-menu` absolute drop-down below it still works (parent needs `position:relative` — keep the rule `#sidebar .panel-header { position: relative; }`; extend to `.editor-header-row .sidebar-project { position: relative; }`).
- Ensure the entity CSS rules (`.tabs` hide, tab-pane show) are unchanged: they apply to `.tabs`, not the new `.editor-header-row`, so nav stays visible in every entity mode.

### 3.3 JS

- **No logic changes.** Verification against existing selectors:
  - `main.js:345-355` `.project-item` clicks — selector-based, unaffected.
  - `main.js:473-478` keyboard 1–5 — selector-based, unaffected.
  - `utils.js:85-114` `updateEntityUI` — selector-based, unaffected.
  - `layout.js` — handles/collapse/fold — only DOM *content* moves; class `sidebar-collapsed/editor-collapsed` semantics unchanged.
  - `updateProjectSidebar` (`utils.js:41`) reads `#proj-name-sidebar`; with the element removed the `if (el)` guard makes it a no-op. Remove the now-unused function and its import/export in `main.js` if present (verify by grep).
- Project dropdown `hidden` toggling (`main.js` project-menu handlers) is id based and unaffected.

### 3.4 Testing

- No new unit tests required (pure DOM relocation; behavior-equivalent). Existing Vitest suites must stay green: `npm test`.
- Manual checklist (`python main.py serve --no-browser`):
  1. Left sidebar shows only the video section (no project name / nav).
  2. Entity switching via click and keys 1–5 identical to before.
  3. 项目 ▾ opens only the dropdown (打开项目 / 新建项目 / 打开目录) and each item works.
  4. Ctrl+B collapses the left sidebar; Ctrl+\\ collapses the editor; nav stays visible in all entity modes while editor is open.
  5. Video search/chips/count-bar unchanged.

## 4. Edge cases / error handling

- **Editor collapsed (Ctrl+\)**: nav row hidden; keyboard 1–5 still switches entity — accepted mitigation (unchanged from today's `#editor` collapse behavior).
- **Sidebar collapsed (Ctrl+B)**: nav remains visible in the right panel (the point of the move — it no longer hides with the list).
- Removing `proj-name-sidebar` must not break `updateProjectSidebar` (guard `if (el)`).
- Empty-state video list (no videos yet) — unaffected (video section already handles it).

## 5. Out of scope / future

- Merging entity nav into one tab strip with the analysis tabs — a later UX refinement.
- Making the nav fold away in ultra-narrow windows.
- Persisting "nav on right" as a session option.

## 6. Review plan

1. Implement as one commit: `feat(ui): move sidebar nav and project menu to editor panel`.
2. Run `npm test` (front-end suites stay green).
3. Manual UI pass per §3.4; no README/docs update needed.