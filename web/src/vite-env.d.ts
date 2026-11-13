/// <reference types="vite/client" />

declare const __ETF_RELEASE_VERSION__: string;

interface ImportMetaEnv {
  readonly VITE_API_MODE?: 'mock' | 'live';
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
