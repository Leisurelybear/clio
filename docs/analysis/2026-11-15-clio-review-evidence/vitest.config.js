import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    setupFiles: ['clio/ui/static/src/__tests__/setup.js'],
    include: ['docs/analysis/2026-11-15-clio-review-evidence/*.test.js'],
  },
});
