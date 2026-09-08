// Правила проверки фронтенда. Запуск: npm run lint (см. package.json).
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "../static"] },
  js.configs.recommended,
  // Проверки с учётом типов: ловят, например, забытый await и «висячие»
  // промисы — самый частый источник молча пропавших ошибок.
  tseslint.configs.recommendedTypeChecked,
  {
    files: ["src/**/*.ts"],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      // Промис без await должен быть отмечен `void`: так видно, что
      // результат игнорируется намеренно.
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": "error",
      // Обработчики событий возвращают значение, которое никому не нужно.
      "@typescript-eslint/no-confusing-void-expression": "off",
      // Пустой catch — обычное дело для localStorage и разбора JSON, но
      // причина должна быть в комментарии.
      "no-empty": ["error", { allowEmptyCatch: true }],
      eqeqeq: ["error", "always"],
      "no-var": "error",
      "prefer-const": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Конфигурационные файлы проверяются без сведений о типах.
    files: ["*.js", "*.ts"],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: { globals: globals.node },
  },
);
