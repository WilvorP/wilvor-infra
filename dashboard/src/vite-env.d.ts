/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_WILVOR_API_BASE_URL?: string;
  readonly VITE_WILVOR_API_TIMEOUT_MS?: string;
  readonly VITE_WILVOR_MAP_STYLE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
