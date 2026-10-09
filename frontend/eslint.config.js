import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // The OpenAPI-derived client is generated; it is excluded like other vendored output.
  globalIgnores(['dist', 'src/api/schema.ts', 'test-results', 'playwright-report']),
  {
    files: ['**/*.{js,jsx,ts,tsx}'],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^_' },
      ],
    },
  },
  {
    // Tooling configuration and browser specs run in Node, not the browser.
    files: ['vite.config.js', 'playwright.config.ts', 'e2e/**'],
    languageOptions: { globals: globals.node },
  },
  {
    // Test helpers and browser specs are never hot-reloaded.
    files: ['src/test/**', '**/*.test.{ts,tsx}', 'e2e/**', 'playwright.config.ts'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
])
