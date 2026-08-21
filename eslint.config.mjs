import globals from "globals";

export default [
  {
    files: ["ui/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
        THREE: "readonly",
        pywebview: "readonly"
      }
    },
    rules: {
      "no-unused-vars": "warn",
      "no-console": "off",
      "no-undef": "error"
    }
  }
];
