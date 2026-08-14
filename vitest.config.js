import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['clio/ui/static/src/__tests__/**/*.test.js'],
    coverage: {
      provider: 'v8',
      include: ['clio/ui/static/src/**/*.js'],
      exclude: ['clio/ui/static/src/__tests__/**'],
      reporter: ['text', 'json-summary'],
      thresholds: {
        // P2-P31: real frontend metrics against an explicit floor. Measured
        // ~31% lines/statements, ~34% functions on a local Node 24 run; keep
        // the gate below measured values so it catches regressions without
        // being flaky across Node 18/22/24.
        lines: 25,
        statements: 25,
        functions: 28,
        branches: 22,
      },
    },
  },
});
