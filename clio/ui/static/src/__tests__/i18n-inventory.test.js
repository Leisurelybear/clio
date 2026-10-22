/**
 * Guard: user-facing Web UI copy must resolve through the shared catalog.
 *
 * Milestone 10 of docs/superpowers/plans/2026-10-22-i18n-completion-plan.md.
 *
 * Comments are stripped before scanning, so a Chinese comment never fails the
 * guard. Only Chinese text that survives comment removal is compared against
 * the explicit allow-list below.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = resolve(HERE, '..');
const REPO_ROOT = resolve(SRC_ROOT, '..', '..', '..', '..');

const CJK_RUN = /[\u4e00-\u9fff]+/g;

// file (repo-relative) -> sorted unique Chinese runs that are allowed to stay
const ALLOWED = {
  // ui/static/src/editor-config.js
  'clio/ui/static/src/editor-config.js': [
    '不限',
    '不限制',
    '像素',
    '分析并发数',
    '分析窗重叠',
    '分析窗长',
    '分析结果子目录',
    '分钟',
    '剪映草稿目录',
    '压缩视频子目录',
    '口播子目录',
    '口播模板文件',
    '口播目标字数',
    '后台系统通知',
    '启用',
    '导出子目录',
    '引擎',
    '整片分析硬顶',
    '日志目录',
    '最大宽度',
    '模型大小',
    '每分钟请求上限',
    '每日最多片段数',
    '每片段最多转录段数',
    '注入语音转录',
    '画布比例',
    '界面语言',
    '目标体积',
    '目标时长',
    '秒',
    '缓存',
    '自动复制草稿',
    '规划子目录',
    '识别语言',
    '调试打印完整',
    '超时',
    '路径',
    '跳过已处理文件',
    '转录子目录',
    '轮询间隔',
    '输出目录',
    '运行设备',
    '追加到',
    '项目背景',
    '额外重试次数',
  ],
  // ui/static/src/editor-refine.js
  'clio/ui/static/src/editor-refine.js': [
    '审阅',
    '正在',
  ],
  // ui/static/src/logs-filter.js
  'clio/ui/static/src/logs-filter.js': [
    '出错',
    '失败',
    '警告',
    '跳过',
    '错误',
  ],
};

function stripComments(source) {
  let out = '';
  let state = 'code';
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    const next = source[i + 1];
    if (state === 'code') {
      if (ch === '/' && next === '/') {
        state = 'line';
        out += '  ';
        i += 1;
      } else if (ch === '/' && next === '*') {
        state = 'block';
        out += '  ';
        i += 1;
      } else {
        if (ch === "'") state = 'single';
        else if (ch === '"') state = 'double';
        else if (ch === '`') state = 'template';
        out += ch;
      }
      continue;
    }
    if (state === 'line') {
      if (ch === '\n') {
        state = 'code';
        out += ch;
      } else {
        out += ' ';
      }
      continue;
    }
    if (state === 'block') {
      if (ch === '*' && next === '/') {
        state = 'code';
        out += '  ';
        i += 1;
      } else {
        out += ch === '\n' ? '\n' : ' ';
      }
      continue;
    }
    if (ch === '\\') {
      out += ch + (next ?? '');
      i += 1;
      continue;
    }
    if (
      (state === 'single' && ch === "'") ||
      (state === 'double' && ch === '"') ||
      (state === 'template' && ch === '`')
    ) {
      state = 'code';
    }
    out += ch;
  }
  return out;
}

function listModules(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    if (entry === '__tests__' || entry === 'locales' || entry === 'node_modules') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      found.push(...listModules(full));
    } else if (entry.endsWith('.js')) {
      found.push(full);
    }
  }
  return found;
}

function chineseRuns(file) {
  const stripped = stripComments(readFileSync(file, 'utf8'));
  return [...new Set(stripped.match(CJK_RUN) ?? [])].sort();
}

describe('Web UI i18n inventory', () => {
  it('keeps no hard-coded Chinese copy outside the allow-list', () => {
    const actual = {};
    for (const file of listModules(SRC_ROOT)) {
      const key = relative(REPO_ROOT, file).split(sep).join('/');
      const runs = chineseRuns(file);
      if (runs.length) actual[key] = runs;
    }

    const unexpected = {};
    for (const [key, runs] of Object.entries(actual)) {
      const extra = runs.filter((run) => !(ALLOWED[key] ?? []).includes(run));
      if (extra.length) unexpected[key] = extra;
    }
    const stale = {};
    for (const [key, runs] of Object.entries(ALLOWED)) {
      const gone = runs.filter((run) => !(actual[key] ?? []).includes(run));
      if (gone.length) stale[key] = gone;
    }

    expect(unexpected).toEqual({});
    expect(stale).toEqual({});
  });
});