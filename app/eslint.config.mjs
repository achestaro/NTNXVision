import js from "@eslint/js";
import tseslint from "typescript-eslint";
import globals from "globals";

// No em dash (U+2014) in user-visible text: string literals, template literals and JSX text
// across the renderer, main process and shared contract. Comments are not checked. Use a
// comma, colon, period or parentheses in prose, and `NA` from lib/format.ts for a missing
// value. Mirrors core/tests/test_no_em_dash.py.
const noEmDash = ["Literal[value=/\u2014/]", "TemplateElement[value.raw=/\u2014/]", "JSXText[value=/\u2014/]"].map(
  (selector) => ({
    selector,
    message: "No em dash in user-visible text: use a comma, colon, period, parentheses or NA.",
  }),
);

// One checkbox: the Runway <Checkbox> (components/Checkbox.tsx) is the only place that
// renders a native checkbox input.
const noBareCheckbox = {
  selector: "JSXOpeningElement[name.name='input']:has(JSXAttribute[name.name='type'][value.value='checkbox'])",
  message: "Use <Checkbox> from components (the Runway checkbox), not a bare <input type=\"checkbox\">.",
};

export default tseslint.config(
  { ignores: ["dist/**", "release/**", "node_modules/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.ts", "**/*.tsx"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    files: ["src/**/*.ts", "src/**/*.tsx"],
    rules: { "no-restricted-syntax": ["error", ...noEmDash, noBareCheckbox] },
  },
  {
    files: ["src/renderer/components/Checkbox.tsx"],
    rules: { "no-restricted-syntax": ["error", ...noEmDash] },
  },
  {
    // Dev scripts: the screenshot runner (Node) and the stub window.ntnx (browser).
    files: ["scripts/**/*.{js,mjs}"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },
  {
    // electron-builder hooks (build/notarize.js) are plain CommonJS run by Node.
    files: ["build/**/*.js"],
    languageOptions: {
      sourceType: "commonjs",
      globals: { require: "readonly", exports: "writable", module: "writable",
                 process: "readonly", console: "readonly", __dirname: "readonly" },
    },
    rules: { "@typescript-eslint/no-require-imports": "off" },
  }
);
