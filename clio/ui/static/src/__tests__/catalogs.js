import en from '../../locales/en.json';
import zhCN from '../../locales/zh-CN.json';
import { setCatalog } from '../i18n.js';

/**
 * Tests assert real user-facing copy, so the shipped catalogs must be loaded.
 * `vi.resetModules()` gives every module a fresh instance, so files that reset
 * the registry must call this again inside `beforeEach` to re-seed.
 */
export function seedCatalogs() {
  setCatalog('zh-CN', zhCN);
  setCatalog('en', en);
}
