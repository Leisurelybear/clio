# Sidebar Nav Relocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the left sidebar's entity nav row and project dropdown menu to the top of the right editor panel, leaving the left sidebar for the video list only.

**Architecture:** Frontend-only HTML/CSS change. The 5 `data-entity` icon items keep `id`/class/`data-entity` so existing selector-based JS wiring (`main.js`, `utils.js`, `layout.js`) keeps working unchanged. New `.editor-header-row` flex row holds the nav + project menu between the editor panel-header and the `.tabs`. The left `#sidebar` keeps only its slim panel-header (collapse button ‹) plus the video section.

**Tech Stack:** HTML (`clio/ui/static/index.html`), CSS (`clio/ui/static/style.css`), Vitest for the frontend test gate.

---

### Task 1: Move nav + project menu HTML into the right editor

**Files:**
- Modify: `clio/ui/static/index.html`

- [ ] **Step 1: Read the current index.html sidebar + editor blocks**

Read `clio/ui/static/index.html` lines 36-102 (sidebar) and 151-179 (editor).

- [ ] **Step 2: Remove the project menu + nav from the sidebar**

In the `<aside id="sidebar">` block, remove the `<div class="sidebar-project">` (contains `btn-collapse-sidebar`, `btn-project-menu`, `#project-menu`) and the entire `<ul id="project-list" class="project-list-icons">…</ul>` nav row.

Keep the `<div class="panel-header">` skeleton, replacing its content with only:
- a panel-header-left icon (reuse the existing grid icon svg) and
- the `#btn-collapse-sidebar` ‹ button.

Leave the `<div class="sidebar-scroll">` video section untouched.

- [ ] **Step 3: Insert the editor-header-row into the editor**

In the `<aside id="editor">` block, insert between the `</div>` closing `.panel-header` and the `<div class="tabs">`:

```html
  <div class="editor-header-row">
    <ul id="project-list" class="project-list-icons">
      <li class="project-item" data-entity="plan" title="打开编排 (plan) 面板">
        <span class="icon"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg></span>
        <span class="name">编排</span>
        <span class="shortcut">1</span>
      </li>
      <li class="project-item" data-entity="config" title="编辑 config.yaml 配置">
        <span class="icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"/></svg></span>
        <span class="name">设置</span>
        <span class="shortcut">2</span>
      </li>
      <li class="project-item" data-entity="run" title="运行流水线: 压缩→分析→口播→vlog 剪辑规划→标号">
        <span class="icon"><svg viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg></span>
        <span class="name">运行</span>
        <span class="shortcut">3</span>
      </li>
      <li class="project-item" data-entity="logs" title="查看服务运行日志">
        <span class="icon"><svg viewBox="0 0 24 24"><polyline points="1 12 1 19 23 19 23 12"/><polyline points="22 8 12 3 2 8 2 8"/><rect x="12" y="15" width="2" height="2"/><rect x="8" y="15" width="2" height="2"/><rect x="4" y="15" width="2" height="2"/></svg></span>
        <span class="name">日志</span>
        <span class="shortcut">4</span>
      </li>
      <li class="project-item" data-entity="tokens" title="AI token 使用统计">
        <span class="icon"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg></span>
        <span class="name">统计</span>
        <span class="shortcut">5</span>
      </li>
    </ul>
    <div class="sidebar-project">
      <button id="btn-project-menu" class="sidebar-btn" type="button" title="项目操作: 打开 / 新建 / 打开当前目录">项目 ▾</button>
      <div id="project-menu" class="project-menu" hidden>
        <button id="btn-open-project" class="project-menu-item" type="button" title="打开已有项目">打开项目</button>
        <button id="btn-new-project" class="project-menu-item" type="button" title="新建项目">新建项目</button>
        <button id="btn-reveal-project-sidebar" class="project-menu-item" type="button" title="在资源管理器中打开当前项目目录">打开目录</button>
      </div>
    </div>
  </div>
```

The content above is the exact nav/menu HTML currently in the sidebar (`index.html:53-79` and `42-50`), just relocated. Do not retype from memory — move the existing nodes verbatim.

- [ ] **Step 4: Verify the sidebar now starts with the slim header + video section**

Read the modified `#sidebar` block (lines ~36-75). Confirm it contains only: slim `panel-header` (icon + `#btn-collapse-sidebar`) then `.sidebar-scroll` with `h3` 视频…`stage-count-bar`. No `#project-list`, no `#btn-project-menu`, no `#proj-name-sidebar`.

- [ ] **Step 5: Commit**

```bash
git add clio/ui/static/index.html
git commit -m "feat(ui): move sidebar nav and project menu to editor panel"
```

---

### Task 2: CSS for the editor-header-row

**Files:**
- Modify: `clio/ui/static/style.css`

- [ ] **Step 1: Add the editor-header-row styles**

Add this block after the `.tabs { … }`/`.tab.active { … }` rules (around `style.css:1132`), before the `#editor.entity-*` rules:

```css
/* Editor header row: entity nav + project menu above the tabs */
#editor .editor-header-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
#editor .editor-header-row .sidebar-project { position: relative; margin-left: auto; display: flex; gap: 4px; align-items: center; }
#editor .editor-header-row .project-menu {
  right: 0; left: auto;
}
```

- [ ] **Step 2: Fix the nav icon row CSS for the new location**

The current `#project-list.project-list-icons` rule (`style.css:561-575`) sets `position: sticky; top: 0;` for the sidebar scroll. Update it to drop the sticky positioning and scope it for the editor header:

```css
/* Editor header: compact entity nav icon row */
#editor #project-list.project-list-icons {
  list-style: none; margin: 0; padding: 4px 0;
  display: flex; gap: 4px; flex: 1; min-width: 0;
}
```

Remove the old `#project-list.project-list-icons { … }` block (lines 561-568) — it is being replaced by the scoped version above. Keep the `.project-list-icons .project-item` rules (569-575) unchanged (they apply to both placements).

- [ ] **Step 3: Remove the now-unused sidebar sticky nav references**

Check `style.css` for `#sidebar #project-list`, `#sidebar .project-list-icons` or `#project-list.project-list-icons` outside the new `#editor` scoped rule. If none remain, nothing else to delete. Confirm the `.project-item` base styles (lines 641-663) still exist unchanged, because `updateEntityUI` active-state styling depends on them.

- [ ] **Step 4: Run the frontend tests**

Run: `npm test`
Expected: PASS (no test references the moved nav DOM; `player-layout.test.js`/`layout.test.js` are CSS/function-level).

- [ ] **Step 5: Commit**

```bash
git add clio/ui/static/style.css
git commit -m "style(ui): add editor-header-row styles for relocated nav"
```

---

### Task 3: Verify JS wiring and dead-code cleanup

**Files:**
- Modify: `clio/ui/static/src/main.js` (only if `updateProjectSidebar` no longer has a DOM target)

- [ ] **Step 1: Confirm selectors still match the moved DOM**

Run: `python -c "import re;d=open('clio/ui/static/index.html',encoding='utf-8').read();print('project-list' in d, 'data-entity' in d, 'btn-open-project' in d, 'btn-reveal-project-sidebar' in d)"`
Expected: `True True True True`

- [ ] **Step 2: Verify `updateProjectSidebar` is safe**

Read `clio/ui/static/src/utils.js` line 80-83. The function reads `#proj-name-sidebar` and guards with `if (el)`. Since the element is gone, it is a no-op. Keep the function (harmless) unless grep shows it is exported/imported — it is exported (`utils.js:128` `updateProjectSidebar`). Leave it; no crash.

- [ ] **Step 3: Verify keyboard shortcuts still target `.project-item`**

Run: `python -c "import re;d=open('clio/ui/static/src/main.js',encoding='utf-8').read();print(re.search(r\"\\$\\$\\(\'\\.project-item\'\\)\", d) is not None, '\\'1\\'' in d)"`
Expected: `True True`

- [ ] **Step 4: Run full frontend test suite**

Run: `npm test`
Expected: PASS (green baseline confirmed before the change too — no suite references the sidebar nav DOM).

- [ ] **Step 5: Commit (only if any source file changed; otherwise skip)**

```bash
git add clio/ui/static/src/main.js
git commit -m "refactor(ui): drop dead project-name sidebar listener"
```

---

### Task 4: Manual verification

**Files:**
- (none — run the app)

- [ ] **Step 1: Start the dev server**

Run: `python main.py serve --no-browser`
Expected: server starts; note the printed URL and API token if auth is enabled.

- [ ] **Step 2: Open the UI and check the left sidebar**

Open the URL. Verify the left sidebar shows only: slim header (icon + ‹), then 视频 section. No project name, no nav icons, no 项目▾.

- [ ] **Step 3: Check the editor header row**

Verify the right panel shows, top to bottom: 编辑器 panel header, then the icon nav (编排/设置/运行/日志/统计 with shortcuts 1-5), then tabs (分析/口播/转录).

- [ ] **Step 4: Exercise entity switching**

Click each nav icon and press keys 1-5. Confirm each switches the right panel view (plan/config/run/logs/tokens/texts) and highlights the active icon.

- [ ] **Step 5: Exercise the project menu**

Click 项目 ▾; verify 打开项目 / 新建项目 / 打开目录 appear and the 打开项目 modal opens; click outside to close.

- [ ] **Step 6: Fold/unfold both panels**

Press Ctrl+B (sidebar folds — nav stays on the right), Ctrl+\ (editor folds — video goes full-width; while folded, press 1-5 and confirm entity state can still change). Unfold both back.

- [ ] **Step 7: Confirm video section unchanged**

Add/search/filter videos; confirm stage-count-bar cells still work as before.