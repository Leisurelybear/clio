import en from '../../locales/en.json';
import zhCN from '../../locales/zh-CN.json';
import { setCatalog } from '../i18n.js';

// Tests assert real user-facing copy, so load the shipped catalogs once per file.
setCatalog('zh-CN', zhCN);
setCatalog('en', en);
